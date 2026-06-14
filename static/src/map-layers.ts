/**
 * Overlay layers + their lazy-load fetchers + popup helpers, on MapLibre
 * GL JS (PR B2). Replaces the Leaflet layer-group version.
 *
 * Owns:
 *   - access           glyph-coded access-point HTML markers (lazy per-state),
 *                       toggled PER TYPE (boat ramp / walk-in / wading / pier /
 *                       parking) -- one bucket per type, independent visibility
 *   - stocked          stocked-water HTML markers (lazy per-state); shown
 *                       when the toggle is on OR the Stocked map style is active
 *   - public-lands     PAD-US parcels (GeoJSON source + fill/line layers,
 *                       bbox-bound viewport fetch)
 *   - visibility setters + lazy-load fns: setAccessTypeVisible / anyAccessVisible
 *     / ensureAccess, setStockedVisible / setStockedStyleActive / ensureStocked,
 *     setPublicLandsVisible
 *   - the popup-html helpers (accessPopupHtml, stockedPopupHtml,
 *     publicLandsPopupHtml) and the marker element factories
 *
 * Sources/layers are added in onMapReady (MapLibre rejects addSource/
 * addLayer before the style `load` fires). Initial visibility reflects
 * the desired-state vars below, which controls.ts sets from saved prefs
 * before `load`.
 */

import maplibregl, { Marker, LayerSpecification } from "maplibre-gl";
import { map, onMapReady } from "./map-setup";
import { getCurrentSt } from "./state";
import { esc } from "./util";
import { makePopup } from "./popups";
import {
  PUBLIC_LANDS_TILES_ENABLED,
  PUBLIC_LANDS_TILES_URL,
  PUBLIC_LANDS_SOURCE_LAYER,
  TRAILS_TILES_ENABLED,
  TRAILS_TILES_URL,
  TRAILS_SOURCE_LAYER,
} from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import { makePoiElement } from "./poi-icons";

// Desired visibility (matches the HTML checkbox defaults; controls.ts
// overrides from saved prefs before the map `load` fires).
let _publicLandsVisible = false;
let _trailsVisible = false;

function vis(on: boolean): "visible" | "none" {
  return on ? "visible" : "none";
}

// -- Access points ------------------------------------------------------
// Markers are makePoiElement discs -- the access TYPE is the glyph
// (sailboat / footprints / waves / dock / P). Each type is an independent
// toggle (lyr-access-<type>), so we bucket markers by type and show only
// the enabled buckets. A feature whose type isn't one of the five buckets
// falls into walk_in (matching the glyph fallback in makePoiElement).

const ACCESS_TYPES = [
  "boat_ramp",
  "walk_in",
  "wading_access",
  "pier",
  "parking",
] as const;
type AccessType = (typeof ACCESS_TYPES)[number];

function _accessBucket(type: string | undefined): AccessType {
  return (ACCESS_TYPES as readonly string[]).includes(type || "")
    ? (type as AccessType)
    : "walk_in";
}

// Per-type desired visibility (controls.ts overrides from saved prefs
// before `load`). All off by default -- access is opt-in.
const _accessTypeVisible: Record<AccessType, boolean> = {
  boat_ramp: false,
  walk_in: false,
  wading_access: false,
  pier: false,
  parking: false,
};

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

interface AccessMarker {
  marker: Marker;
  type: AccessType;
  shown: boolean; // currently added to the map
}
let accessMarkers: AccessMarker[] = [];
let accessLoadedState: string | null = null;
let accessLoading = false;

// Perf guard: live DNR feeds can deliver 1000+ access points per state.
// Above this count the markers stay hidden until the user zooms in --
// at state zoom that many HTML markers are decoration that janks panning.
// The gate counts only markers whose type is currently enabled, so turning
// on a single sparse type (e.g. boat ramps) isn't blocked by a dense one.
const ACCESS_GATE_ZOOM = 10;
const ACCESS_GATE_COUNT = 300;

/** True if any access type is toggled on -- gates the lazy per-state load. */
export function anyAccessVisible(): boolean {
  return ACCESS_TYPES.some((t) => _accessTypeVisible[t]);
}

function _enabledAccessCount(): number {
  let n = 0;
  for (const am of accessMarkers) if (_accessTypeVisible[am.type]) n++;
  return n;
}

function _applyAccessVisibility(): void {
  // One zoom gate for the whole enabled set: below it, a too-dense set stays
  // hidden until the user zooms in.
  const gatePass =
    _enabledAccessCount() <= ACCESS_GATE_COUNT || map.getZoom() >= ACCESS_GATE_ZOOM;
  for (const am of accessMarkers) {
    const should = gatePass && _accessTypeVisible[am.type];
    if (should === am.shown) continue;
    if (should) am.marker.addTo(map);
    else am.marker.remove();
    am.shown = should;
  }
}

/** Wired to each per-type lyr-access-<type> checkbox. */
export function setAccessTypeVisible(type: string, on: boolean): void {
  const bucket = _accessBucket(type);
  _accessTypeVisible[bucket] = on;
  _applyAccessVisibility();
}

// Cross the zoom gate -> show/hide the large-set markers.
map.on("zoomend", _applyAccessVisibility);

export async function ensureAccess(state: string): Promise<void> {
  if (accessLoadedState === state || accessLoading) return;
  accessLoading = true;
  try {
    const fc: GeoJsonFeatureCollection<AccessFeatureProps> = await fetch(
      `/api/access?state=${state}`,
    ).then((r) => r.json());
    for (const am of accessMarkers) am.marker.remove();
    accessMarkers = [];
    for (const f of fc.features || []) {
      const c =
        f.geometry && "coordinates" in f.geometry
          ? (f.geometry.coordinates as [number, number])
          : null;
      const p = f.properties || ({} as AccessFeatureProps);
      if (!c || c.length < 2) continue;
      const bucket = _accessBucket(p.type);
      const el = makePoiElement(bucket);
      // Selecting an access point is a POI click -> close the rail panel.
      el.addEventListener("click", () =>
        document.dispatchEvent(new Event("bl:poi-open")),
      );
      const m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([c[0], c[1]]) // GeoJSON is already [lng, lat]
        .setPopup(makePopup().setHTML(accessPopupHtml(p)));
      accessMarkers.push({ marker: m, type: bucket, shown: false });
    }
    _applyAccessVisibility(); // adds enabled buckets if the zoom-gate passes
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

// -- Stocked waters -----------------------------------------------------
// Curated + live stocking points (one pin each). Shown when EITHER the
// "Stocked waters" toggle is on OR the Stocked map style is active -- so the
// style surfaces real stocking locations on top of the stocked-stream color.

let stockedMarkers: Marker[] = [];
let stockedLoadedState: string | null = null;
let stockedLoading = false;
let _stockedToggle = false; // the lyr-stocked checkbox
let _stockedStyleActive = false; // the Stocked map style

export function stockedPopupHtml(p: StockedFeatureProps): string {
  const species = (p.species || []).join(", ");
  const meta = [species, p.category ? esc(p.category) : "",
                p.season ? esc(p.season) : ""].filter(Boolean).join(" &middot; ");
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">Stocking schedule &rarr;</a></div>`
    : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.water || "Stocked water")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    link +
    `</div>`
  );
}

function _stockedShouldShow(): boolean {
  return _stockedToggle || _stockedStyleActive;
}

function _applyStockedVisibility(): void {
  const on = _stockedShouldShow();
  for (const m of stockedMarkers) {
    if (on) m.addTo(map);
    else m.remove();
  }
  if (on) ensureStocked(getCurrentSt());
}

/** Wired to the lyr-stocked checkbox (the wireLayerToggle setter). */
export function setStockedVisible(on: boolean): void {
  _stockedToggle = on;
  _applyStockedVisibility();
}

/** Force-shows the stocked-waters layer regardless of the toggle. (Previously
 *  driven by the retired "Stocked" map-style; retained for callers that want to
 *  force the layer on.) */
export function setStockedStyleActive(on: boolean): void {
  _stockedStyleActive = on;
  _applyStockedVisibility();
}

/** State changed: drop the cache and reload only if the layer is showing. */
export function refreshStockedForState(state: string): void {
  stockedLoadedState = null;
  if (_stockedShouldShow()) ensureStocked(state);
}

export async function ensureStocked(state: string): Promise<void> {
  if (stockedLoadedState === state || stockedLoading) return;
  stockedLoading = true;
  try {
    const fc: GeoJsonFeatureCollection<StockedFeatureProps> = await fetch(
      `/api/stocking?state=${state}`,
    ).then((r) => r.json());
    for (const m of stockedMarkers) m.remove();
    stockedMarkers = [];
    const show = _stockedShouldShow();
    for (const f of fc.features || []) {
      const c =
        f.geometry && "coordinates" in f.geometry
          ? (f.geometry.coordinates as [number, number])
          : null;
      const p = f.properties || ({} as StockedFeatureProps);
      if (!c || c.length < 2) continue;
      const el = makePoiElement("stocked");
      // Selecting a stocked water is a POI click -> close the rail panel.
      el.addEventListener("click", () =>
        document.dispatchEvent(new Event("bl:poi-open")),
      );
      const m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([c[0], c[1]])
        .setPopup(makePopup().setHTML(stockedPopupHtml(p)));
      stockedMarkers.push(m);
      if (show) m.addTo(map);
    }
    stockedLoadedState = state;
  } catch (_) {
    /* leave empty; user can re-toggle to retry */
  } finally {
    stockedLoading = false;
  }
}

// -- Dams (NID) ---------------------------------------------------------
// USACE National Inventory of Dams, one brand-blue disc with the dam glyph.
// A single national source queried per state via /api/dams; some states
// carry thousands of dams, so it uses the same zoom/count perf gate as the
// access layer (hidden below the gate until the user zooms in).

let _damsVisible = false;
let damMarkers: Marker[] = [];
let damsLoadedState: string | null = null;
let damsLoading = false;
let _damsShown = false;
const DAMS_GATE_ZOOM = 10;
const DAMS_GATE_COUNT = 300;

export function damPopupHtml(p: DamFeatureProps): string {
  const meta = [
    p.river ? `on ${esc(p.river)}` : "",
    p.owner ? esc(p.owner) : "",
    p.year ? esc(p.year) : "",
    p.height_ft ? `${p.height_ft} ft` : "",
  ]
    .filter(Boolean)
    .join(" &middot; ");
  const purposes = p.purposes
    ? `<div class="ap-notes">${esc(p.purposes)}</div>`
    : "";
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">NID record &rarr;</a></div>`
    : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Dam")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    purposes +
    link +
    `</div>`
  );
}

function _damsShouldShow(): boolean {
  return (
    _damsVisible &&
    (damMarkers.length <= DAMS_GATE_COUNT || map.getZoom() >= DAMS_GATE_ZOOM)
  );
}

function _applyDamsVisibility(): void {
  const on = _damsShouldShow();
  if (on === _damsShown) return;
  for (const m of damMarkers) {
    if (on) m.addTo(map);
    else m.remove();
  }
  _damsShown = on;
}

export function setDamsVisible(on: boolean): void {
  _damsVisible = on;
  _applyDamsVisibility();
}

map.on("zoomend", _applyDamsVisibility);

export async function ensureDams(state: string): Promise<void> {
  if (damsLoadedState === state || damsLoading) return;
  damsLoading = true;
  try {
    const fc: GeoJsonFeatureCollection<DamFeatureProps> = await fetch(
      `/api/dams?state=${state}`,
    ).then((r) => r.json());
    for (const m of damMarkers) m.remove();
    damMarkers = [];
    _damsShown = false;
    for (const f of fc.features || []) {
      const c =
        f.geometry && "coordinates" in f.geometry
          ? (f.geometry.coordinates as [number, number])
          : null;
      const p = f.properties || ({} as DamFeatureProps);
      if (!c || c.length < 2) continue;
      const el = makePoiElement("dam");
      el.addEventListener("click", () =>
        document.dispatchEvent(new Event("bl:poi-open")),
      );
      const m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat([c[0], c[1]])
        .setPopup(makePopup().setHTML(damPopupHtml(p)));
      damMarkers.push(m);
    }
    _applyDamsVisibility(); // adds them only if visible + zoom-gate passes
    damsLoadedState = state;
  } catch (_) {
    /* leave empty; user can re-toggle to retry */
  } finally {
    damsLoading = false;
  }
}

export function resetDamsLoadedState(): void {
  damsLoadedState = null;
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

// -- River trails (USGS National Digital Trails) -------------------------
// A static PMTiles LINE layer (built-time filtered to trails running
// alongside the stream network), same pattern as public-lands. Dashed
// amber so it reads as a footpath distinct from the tier-colored streams.

const TRAILS_SRC_LAYER = { "source-layer": TRAILS_SOURCE_LAYER };

export function trailPopupHtml(p: TrailProps): string {
  const meta = [
    p.trail_type ? esc(p.trail_type) : "",
    p.surface ? esc(p.surface) : "",
    p.length_mi ? `${p.length_mi} mi` : "",
  ]
    .filter(Boolean)
    .join(" &middot; ");
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Trail")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    `</div>`
  );
}

onMapReady(() => {
  if (!TRAILS_TILES_ENABLED) return;
  ensurePmtilesProtocol();
  map.addSource("trails", {
    type: "vector",
    url: `pmtiles://${TRAILS_TILES_URL}`,
  });
  map.addLayer({
    id: "trails-line",
    type: "line",
    source: "trails",
    ...TRAILS_SRC_LAYER,
    layout: {
      visibility: vis(_trailsVisible),
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "#b45309",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 14, 2.4],
      "line-opacity": 0.85,
      "line-dasharray": [2, 1.5],
    },
  } as LayerSpecification);
  const popup = makePopup();
  map.on("click", "trails-line", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    popup
      .setLngLat(e.lngLat)
      .setHTML(trailPopupHtml((f.properties || {}) as TrailProps))
      .addTo(map);
  });
});

export function setTrailsVisible(on: boolean): void {
  _trailsVisible = on;
  if (map.getLayer("trails-line")) {
    map.setLayoutProperty("trails-line", "visibility", vis(on));
  }
}
