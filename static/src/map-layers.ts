/**
 * Overlay layers + their lazy-load fetchers + popup helpers, on MapLibre
 * GL JS (PR B2). Replaces the Leaflet layer-group version.
 *
 * Owns:
 *   - access           type-coded access-point HTML markers (lazy per-state)
 *   - public-lands     PAD-US parcels (GeoJSON source + fill/line layers,
 *                       bbox-bound viewport fetch)
 *   - visibility setters + lazy-load fns: setAccessVisible / ensureAccess,
 *     setPublicLandsVisible
 *   - the popup-html helpers (accessPopupHtml, publicLandsPopupHtml) and
 *     the access-marker element factory
 *
 * Sources/layers are added in onMapReady (MapLibre rejects addSource/
 * addLayer before the style `load` fires). Initial visibility reflects
 * the desired-state vars below, which controls.ts sets from saved prefs
 * before `load`.
 */

import maplibregl, { Marker, LayerSpecification } from "maplibre-gl";
import { map, onMapReady } from "./map-setup";
import { esc } from "./util";
import { makePopup } from "./popups";
import {
  PUBLIC_LANDS_TILES_ENABLED,
  PUBLIC_LANDS_TILES_URL,
  PUBLIC_LANDS_SOURCE_LAYER,
} from "./config";
import { ensurePmtilesProtocol } from "./tiles";

// Desired visibility (matches the HTML checkbox defaults; controls.ts
// overrides from saved prefs before the map `load` fires).
let _publicLandsVisible = false;
let _accessVisible = false;

function vis(on: boolean): "visible" | "none" {
  return on ? "visible" : "none";
}

// -- Access points ------------------------------------------------------

const ACCESS_TYPE_META: Record<string, { glyph: string; color: string }> = {
  boat_ramp: { glyph: "B", color: "#d97706" },
  walk_in: { glyph: "W", color: "#0891b2" },
  wading_access: { glyph: "W", color: "#0891b2" },
  pier: { glyph: "P", color: "#7c3aed" },
  parking: { glyph: "P", color: "#475569" },
};

/** The DOM element for an access marker (the old divIcon HTML). */
export function makeAccessElement(type: string | undefined): HTMLElement {
  const meta = ACCESS_TYPE_META[type ?? "walk_in"] || ACCESS_TYPE_META.walk_in;
  const wrap = document.createElement("div");
  wrap.className = "access-marker";
  wrap.innerHTML = `<div class="access-marker-pin" style="background:${meta.color}">${esc(meta.glyph)}</div>`;
  return wrap;
}

export function accessPopupHtml(p: AccessFeatureProps): string {
  const accessChip = p.access
    ? `<span class="ap-chip ap-chip-${esc(p.access)}">${esc(p.access)}</span>`
    : "";
  const typeLabel = String(p.type || "walk_in").replace(/_/g, " ");
  const notes = p.notes ? `<div class="ap-notes">${esc(p.notes)}</div>` : "";
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">Agency info &rarr;</a></div>`
    : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Access point")}</div>` +
    `<div class="ap-meta">${esc(typeLabel)}${accessChip}</div>` +
    notes +
    link +
    `</div>`
  );
}

let accessMarkers: Marker[] = [];
let accessLoadedState: string | null = null;
let accessLoading = false;

export function setAccessVisible(on: boolean): void {
  _accessVisible = on;
  for (const m of accessMarkers) {
    if (on) m.addTo(map);
    else m.remove();
  }
}

export async function ensureAccess(state: string): Promise<void> {
  if (accessLoadedState === state || accessLoading) return;
  accessLoading = true;
  try {
    const fc: GeoJsonFeatureCollection<AccessFeatureProps> = await fetch(
      `/api/access?state=${state}`,
    ).then((r) => r.json());
    for (const m of accessMarkers) m.remove();
    accessMarkers = [];
    for (const f of fc.features || []) {
      const c =
        f.geometry && "coordinates" in f.geometry
          ? (f.geometry.coordinates as [number, number])
          : null;
      const p = f.properties || ({} as AccessFeatureProps);
      if (!c || c.length < 2) continue;
      const el = makeAccessElement(p.type);
      // Selecting an access point is a POI click -> close the rail panel.
      el.addEventListener("click", () =>
        document.dispatchEvent(new Event("bl:poi-open")),
      );
      const m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([c[0], c[1]]) // GeoJSON is already [lng, lat]
        .setPopup(makePopup().setHTML(accessPopupHtml(p)));
      accessMarkers.push(m);
      if (_accessVisible) m.addTo(map);
    }
    accessLoadedState = state;
  } catch (_) {
    /* leave empty; user can re-toggle to retry */
  } finally {
    accessLoading = false;
  }
}

export function resetAccessLoadedState(): void {
  accessLoadedState = null;
}

// -- Public lands (PAD-US) ----------------------------------------------
// Tier-keyed styling via `match` expressions on the public_access prop:
// OA = open access (green), RA = restricted (dashed yellow), XA/UK hidden.

const PA_ACCESS_LABEL: Record<PublicAccessTier, string> = {
  OA: "Open access",
  RA: "Restricted access",
  XA: "Closed",
  UK: "Unknown",
};

export function publicLandsPopupHtml(p: PublicLandsProps): string {
  const tierCode = (p.public_access as PublicAccessTier) || "UK";
  const tierLabel = PA_ACCESS_LABEL[tierCode] || PA_ACCESS_LABEL.UK;
  const tierChip = `<span class="ap-chip pa-chip-${esc(tierCode)}">${esc(tierLabel)}</span>`;
  const lines = [
    `<div class="ap-name">${esc(p.unit_name || "Public land parcel")}</div>`,
  ];
  const sub: string[] = [];
  const manager = (p as { manager_name?: string }).manager_name;
  if (manager) sub.push(esc(manager));
  if (p.designation) sub.push(esc(p.designation));
  if (sub.length) lines.push(`<div class="ap-meta">${sub.join(" &middot; ")}</div>`);
  lines.push(`<div class="ap-meta" style="margin-top:6px">${tierChip}</div>`);
  const stateNm = (p as { state_nm?: string }).state_nm;
  if (stateNm) lines.push(`<div class="ap-notes">${esc(stateNm)}</div>`);
  return `<div class="ap-popup">${lines.join("")}</div>`;
}

const PL_SRC_LAYER = { "source-layer": PUBLIC_LANDS_SOURCE_LAYER };

onMapReady(() => {
  // GeoJSON fallback retired in M3 — public lands is served only as static
  // PMTiles on R2 (read via pmtiles://, configured by
  // VITE_PUBLIC_LANDS_TILES_URL). MapLibre fetches/decodes the tiles itself.
  if (!PUBLIC_LANDS_TILES_ENABLED) return;
  ensurePmtilesProtocol();
  map.addSource("public-lands", {
    type: "vector",
    url: `pmtiles://${PUBLIC_LANDS_TILES_URL}`,
  });
  map.addLayer({
    id: "public-lands-fill",
    type: "fill",
    source: "public-lands",
    ...PL_SRC_LAYER,
    layout: { visibility: vis(_publicLandsVisible) },
    paint: {
      "fill-color": [
        "match",
        ["get", "public_access"],
        "OA", "#2d6a4f",
        "RA", "#eab308",
        "#000",
      ],
      "fill-opacity": [
        "match",
        ["get", "public_access"],
        "OA", 0.28,
        "RA", 0.22,
        0,
      ],
    },
  } as LayerSpecification);
  map.addLayer({
    id: "public-lands-line",
    type: "line",
    source: "public-lands",
    ...PL_SRC_LAYER,
    layout: { visibility: vis(_publicLandsVisible) },
    paint: {
      "line-color": [
        "match",
        ["get", "public_access"],
        "OA", "#1b4332",
        "RA", "#854d0e",
        "#000",
      ],
      "line-width": ["match", ["get", "public_access"], "OA", 0.8, "RA", 1.0, 0],
      "line-opacity": ["match", ["get", "public_access"], "OA", 1, "RA", 1, 0],
      "line-dasharray": ["match", ["get", "public_access"], "RA", ["literal", [4, 4]], ["literal", [1, 0]]],
    },
  } as LayerSpecification);
  const popup = makePopup();
  map.on("click", "public-lands-fill", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    popup
      .setLngLat(e.lngLat)
      .setHTML(publicLandsPopupHtml((f.properties || {}) as PublicLandsProps))
      .addTo(map);
  });
});

export function setPublicLandsVisible(on: boolean): void {
  _publicLandsVisible = on;
  for (const id of ["public-lands-fill", "public-lands-line"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis(on));
  }
}
