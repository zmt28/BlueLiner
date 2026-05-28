/**
 * Overlay layer groups + their lazy-load fetchers + popup helpers.
 * Extracted from the legacy app.js (PR B1e).
 *
 * Owns:
 *   - troutLayer            state-wide trout streams (lazy)
 *   - accessLayer           type-coded access-point markers (lazy)
 *   - publicLandsLayer      PAD-US parcels (bbox-bound, viewport)
 *   - riverLinesLayer       container for NLDI flowlines (populated
 *                            by the rivers code in app.js for now)
 *   - riversLayer           container for gauge-condition markers
 *                            (populated by renderRivers in app.js)
 *   - pinsLayer             container for saved-pin markers
 *                            (populated by the pin code in app.js)
 *   - the lazy-load functions: ensureTrout, ensureAccess, loadPublicLands
 *   - the per-layer style + popup-html helpers + the constants they
 *     depend on (ACCESS_TYPE_META, PUBLIC_LANDS_STYLE, PA_ACCESS_LABEL)
 *
 * Window-bridged for the still-monolithic app.js:
 *   - the six layer objects (toggled by layer-visibility checkboxes
 *     in the controls panel; populated/referenced by viewport
 *     loaders + the renderRivers / pins code in app.js)
 *   - ensureTrout, ensureAccess, loadPublicLands (called from layer-
 *     toggle handlers + moveend debouncers)
 *   - makeAccessIcon, accessPopupHtml, publicLandsPopupHtml (read
 *     by the lazy-load fetchers themselves, but exposed so other
 *     migrations can call them too)
 */

import * as L from "leaflet";
import type { Feature, GeometryObject } from "geojson";
import { map } from "./map-setup";
import { esc, popupOpts } from "./util";

// -- Trout streams ------------------------------------------------------

// Whole-state trout-stream geometry. Large; loaded only when the user
// toggles the layer on (ensureTrout) and once per state, so the initial
// map (layer off by default) is never blocked by a multi-MB parse.
export const troutLayer: L.GeoJSON = L.geoJSON(null, {
  style: { color: "#1abc9c", weight: 2.5, opacity: 0.7 },
  onEachFeature: (f: GeoJsonFeature<TroutFeatureProps>, l: L.Layer) => {
    const p = f.properties || {};
    const n = p.NAME || p.GNIS_Name || p.STream_Nam;
    if (n) l.bindTooltip(String(n), { sticky: true });
  },
});

let troutLoadedState: string | null = null;
let troutLoading = false;

export async function ensureTrout(state: string): Promise<void> {
  if (troutLoadedState === state || troutLoading) return;
  troutLoading = true;
  try {
    const t = await fetch(`/api/trout?state=${state}`).then((r) => r.json());
    troutLayer.clearLayers();
    troutLayer.addData(t);
    troutLoadedState = state;
  } catch (_) {
    /* leave layer empty; user can re-toggle to retry */
  } finally {
    troutLoading = false;
  }
}

// -- Access points ------------------------------------------------------

// Type-coded markers, lazy-loaded per state via /api/access?state=.
// Different glyph + tone per access type so a boat ramp is visually
// distinct from a walk-in trail at a glance.
const ACCESS_TYPE_META: Record<string, { glyph: string; color: string }> = {
  boat_ramp: { glyph: "B", color: "#d97706" },
  walk_in: { glyph: "W", color: "#0891b2" },
  wading_access: { glyph: "W", color: "#0891b2" },
  pier: { glyph: "P", color: "#7c3aed" },
  parking: { glyph: "P", color: "#475569" },
};
export const accessLayer: L.LayerGroup = L.layerGroup();

export function makeAccessIcon(type: string | undefined): L.DivIcon {
  const meta = ACCESS_TYPE_META[type ?? "walk_in"] || ACCESS_TYPE_META.walk_in;
  return L.divIcon({
    className: "access-marker",
    html:
      `<div class="access-marker-pin" style="background:${meta.color}">` +
      `${esc(meta.glyph)}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
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

let accessLoadedState: string | null = null;
let accessLoading = false;

export async function ensureAccess(state: string): Promise<void> {
  if (accessLoadedState === state || accessLoading) return;
  accessLoading = true;
  try {
    const fc: GeoJsonFeatureCollection<AccessFeatureProps> = await fetch(
      `/api/access?state=${state}`,
    ).then((r) => r.json());
    accessLayer.clearLayers();
    for (const f of fc.features || []) {
      const c =
        f.geometry && "coordinates" in f.geometry
          ? (f.geometry.coordinates as [number, number])
          : null;
      const p = f.properties || ({} as AccessFeatureProps);
      if (!c || c.length < 2) continue;
      const m = L.marker([c[1], c[0]], { icon: makeAccessIcon(p.type) });
      m.bindPopup(accessPopupHtml(p), popupOpts());
      accessLayer.addLayer(m);
    }
    accessLoadedState = state;
  } catch (_) {
    /* leave layer empty; user can re-toggle to retry */
  } finally {
    accessLoading = false;
  }
}

// -- Public lands (PAD-US) ----------------------------------------------

// Vector polygons keyed off the `public_access` tier rather than the
// manager type -- the angler's primary question is "can I walk in
// here?", not "is this BLM or USFS?" Two visual tiers: green for OA
// (Open Access) and dashed yellow for RA (Restricted -- permit,
// walk-in, seasonal). UK/XA features are filtered out at build time,
// not rendered. Loaded per-viewport via /api/public_lands?bbox= --
// same lazy bbox-bound pattern as clickable streams.
const PUBLIC_LANDS_STYLE: Record<PublicAccessTier, L.PathOptions> = {
  OA: {
    fillColor: "#2d6a4f",
    color: "#1b4332",
    fillOpacity: 0.28,
    weight: 0.8,
  },
  RA: {
    fillColor: "#eab308",
    color: "#854d0e",
    fillOpacity: 0.22,
    weight: 1.0,
    dashArray: "4,4",
  },
  XA: { fillColor: "#000", color: "#000", fillOpacity: 0, weight: 0 },
  UK: { fillColor: "#000", color: "#000", fillOpacity: 0, weight: 0 },
};
const PUBLIC_LANDS_DEFAULT_STYLE = PUBLIC_LANDS_STYLE.OA;

function publicLandsStyle(
  feature?: Feature<GeometryObject, PublicLandsProps>,
): L.PathOptions {
  const tier =
    (feature?.properties?.public_access as PublicAccessTier | undefined) || "OA";
  return PUBLIC_LANDS_STYLE[tier] || PUBLIC_LANDS_DEFAULT_STYLE;
}

// Access-tier chip labels for the popup. PAD-US codes are terse;
// expand to legible strings + map to chip CSS variants.
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
  if (sub.length) {
    lines.push(`<div class="ap-meta">${sub.join(" &middot; ")}</div>`);
  }
  lines.push(`<div class="ap-meta" style="margin-top:6px">${tierChip}</div>`);
  const stateNm = (p as { state_nm?: string }).state_nm;
  if (stateNm) {
    lines.push(`<div class="ap-notes">${esc(stateNm)}</div>`);
  }
  return `<div class="ap-popup">${lines.join("")}</div>`;
}

export const publicLandsLayer: L.GeoJSON = L.geoJSON(null, {
  style: publicLandsStyle,
  onEachFeature: (f, layer) => {
    layer.bindPopup(
      publicLandsPopupHtml((f.properties || {}) as PublicLandsProps),
      popupOpts(),
    );
  },
});

// Public-lands fetch: bbox-bound, debounced on moveend, zoom-gated.
// Mirrors loadClickableStreams contract: skip when toggled off, skip
// at country-scale bboxes, replace the layer's contents wholesale on
// each fetch (parcels are sparse enough at zoom 8+ that we don't
// need the streams' "merge-with-loaded" gymnastics). Matches
// STREAM_MIN_ZOOM and RIVER_LINE_MIN_ZOOM so all three layer
// families appear/hide at the same zoom boundary.
const PUBLIC_LANDS_MIN_ZOOM = 9;
let _publicLandsReqId = 0;

export async function loadPublicLands(): Promise<void> {
  if (!map.hasLayer(publicLandsLayer)) return;
  if (map.getZoom() < PUBLIC_LANDS_MIN_ZOOM) {
    // Don't clear here: letting the previous frame's parcels linger
    // across the zoom-threshold boundary eliminates the blink-off-
    // blink-on flash users hit when pinching from zoom 10 to zoom 8.
    return;
  }
  const b = map.getBounds();
  // 4° cap matching loadClickableStreams.
  if (b.getEast() - b.getWest() > 4 || b.getNorth() - b.getSouth() > 4) return;
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((x) => x.toFixed(4))
    .join(",");
  const reqId = ++_publicLandsReqId;
  try {
    const fc = await fetch(
      `/api/public_lands?bbox=${bbox}&zoom=${map.getZoom()}`,
    ).then((r) => r.json());
    if (reqId !== _publicLandsReqId) return; // newer pan superseded us
    publicLandsLayer.clearLayers();
    publicLandsLayer.addData(fc);
  } catch (_) {
    /* transient; next moveend retries */
  }
}

// -- Container layer groups ---------------------------------------------
// These are bare LayerGroups that other code (renderRivers, the pin
// fetcher, the river-line fetcher) populates. Adding to map at module
// init means the order matches the legacy app.js -- riverLines and
// rivers sit above public-lands + access by default.

export const riverLinesLayer: L.LayerGroup = L.layerGroup().addTo(map);
export const riversLayer: L.LayerGroup = L.layerGroup().addTo(map);
export const pinsLayer: L.LayerGroup = L.layerGroup().addTo(map);

// -- Window bridge for legacy app.js -----------------------------------

declare global {
  interface Window {
    troutLayer: L.GeoJSON;
    accessLayer: L.LayerGroup;
    publicLandsLayer: L.GeoJSON;
    riverLinesLayer: L.LayerGroup;
    riversLayer: L.LayerGroup;
    pinsLayer: L.LayerGroup;
    ensureTrout: typeof ensureTrout;
    ensureAccess: typeof ensureAccess;
    loadPublicLands: typeof loadPublicLands;
    makeAccessIcon: typeof makeAccessIcon;
    accessPopupHtml: typeof accessPopupHtml;
    publicLandsPopupHtml: typeof publicLandsPopupHtml;
  }
}

window.troutLayer = troutLayer;
window.accessLayer = accessLayer;
window.publicLandsLayer = publicLandsLayer;
window.riverLinesLayer = riverLinesLayer;
window.riversLayer = riversLayer;
window.pinsLayer = pinsLayer;
window.ensureTrout = ensureTrout;
window.ensureAccess = ensureAccess;
window.loadPublicLands = loadPublicLands;
window.makeAccessIcon = makeAccessIcon;
window.accessPopupHtml = accessPopupHtml;
window.publicLandsPopupHtml = publicLandsPopupHtml;
