/**
 * Clickable-stream network on MapLibre GL JS (PR B2). The per-viewport
 * NHDPlus reach layer users click to open the river panel (or the
 * ungauged-stream card).
 *
 * Source modes (MVT spike, Path A): when VITE_STREAM_TILES_URL is set the
 * "clickable-streams" source is a VECTOR source backed by a static PMTiles
 * archive on R2 (read via the pmtiles:// protocol); otherwise it's the
 * per-viewport GeoJSON source fed by /api/clickable_streams. Both use the
 * same two line layers (styled visible + transparent fat "hit" casing),
 * promoteId levelpathid, and click/highlight code — only the source +
 * source-layer + the fetch-vs-tiles bookkeeping differ.
 *
 * MapLibre specifics vs the old Leaflet version:
 *   - Two line layers off one source: a styled visible line and a
 *     transparent fat "hit" casing for touch. Clicks bind to the hit layer.
 *   - Color is a data-driven `match` on trout_class (or flat grey in
 *     conditions mode); width an `interpolate` on streamorder. The
 *     trout/conditions toggle is one setPaintProperty call.
 *   - Selection highlight is feature-state ("selected"); re-applied
 *     after each setData so it persists/extends as the user pans.
 */

import maplibregl, { ExpressionSpecification, LayerSpecification } from "maplibre-gl";
import { map, onMapReady, getGeoJSON } from "./map-setup";
import { esc } from "./util";
import { STREAM_TILES_ENABLED, STREAM_TILES_URL, STREAM_SOURCE_LAYER } from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import {
  prepareRiverPanel,
  commitRiverPanelOpen,
  openRiverPanel,
} from "./river-panel";

const EMPTY_FC: GeoJsonFeatureCollection = { type: "FeatureCollection", features: [] };
const STREAM_MIN_ZOOM = 9;

const STREAM_CLASS_COLORS: Record<string, string> = {
  class_a: "#b8860b",
  wilderness: "#117a65",
  wild_reproduction: "#1e8449",
  stocked: "#2c6fbf",
  designated: "#27ae60",
};

export const STREAM_CLASS_LABEL: Record<string, string> = {
  class_a: "Class A wild trout",
  wilderness: "Wilderness trout",
  wild_reproduction: "Wild reproduction",
  stocked: "Stocked trout",
  designated: "Designated trout",
};

let streamColorMode: "trout" | "conditions" = "trout";
let _streamsVisible = true; // lyr-fishable default checked

export function setStreamColorMode(mode: "trout" | "conditions"): void {
  streamColorMode = mode;
  window.streamColorMode = mode;
}

/** Per-class color, also used for the ungauged-card badge. */
export function streamColor(p: ClickableStreamProps): string {
  if (streamColorMode === "conditions") return "#9aa7b8";
  const cls = p.trout_class;
  return (cls && STREAM_CLASS_COLORS[String(cls)]) || "#8a9bb0";
}

// -- Paint expressions ------------------------------------------------

const TROUT_COLOR_MATCH: ExpressionSpecification = [
  "match",
  ["get", "trout_class"],
  "class_a", "#b8860b",
  "wilderness", "#117a65",
  "wild_reproduction", "#1e8449",
  "stocked", "#2c6fbf",
  "designated", "#27ae60",
  "#8a9bb0",
] as unknown as ExpressionSpecification;

function colorExpr(): ExpressionSpecification {
  const base = streamColorMode === "conditions" ? "#9aa7b8" : TROUT_COLOR_MATCH;
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    "#e74c3c",
    base,
  ] as unknown as ExpressionSpecification;
}

const WIDTH_EXPR: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  8,
  ["interpolate", ["linear"], ["coalesce", ["get", "streamorder"], 3], 1, 4, 7, 7],
] as unknown as ExpressionSpecification;

const OPACITY_EXPR: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  0.95,
  0.8,
] as unknown as ExpressionSpecification;

function visStr(on: boolean): "visible" | "none" {
  return on ? "visible" : "none";
}

// -- Source + layers --------------------------------------------------

// `source-layer` is required for a vector source and forbidden for geojson;
// spread this into the layer specs so the same code serves both modes.
const SRC_LAYER = STREAM_TILES_ENABLED ? { "source-layer": STREAM_SOURCE_LAYER } : {};

onMapReady(() => {
  if (STREAM_TILES_ENABLED) {
    // Path A: static PMTiles on R2, read via the pmtiles:// protocol
    // (HTTP range requests straight to the CDN). MapLibre fetches +
    // caches + decodes tiles itself — no per-viewport GeoJSON fetch.
    ensurePmtilesProtocol();
    map.addSource("clickable-streams", {
      type: "vector",
      url: `pmtiles://${STREAM_TILES_URL}`,
      promoteId: "levelpathid",
    });
  } else {
    map.addSource("clickable-streams", {
      type: "geojson",
      data: EMPTY_FC,
      promoteId: "levelpathid",
    });
  }
  map.addLayer({
    id: "clickable-streams",
    type: "line",
    source: "clickable-streams",
    ...SRC_LAYER,
    layout: { visibility: visStr(_streamsVisible), "line-cap": "round" },
    paint: {
      "line-color": colorExpr(),
      "line-width": WIDTH_EXPR,
      "line-opacity": OPACITY_EXPR,
    },
  } as LayerSpecification);
  // Transparent fat casing for touch targets; clicks bind here.
  map.addLayer({
    id: "clickable-streams-hit",
    type: "line",
    source: "clickable-streams",
    ...SRC_LAYER,
    layout: { visibility: visStr(_streamsVisible), "line-cap": "round" },
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 },
  } as LayerSpecification);
  // In tile mode, rebuild the loaded-reach sets + re-apply highlight as new
  // tiles arrive (the vector-tile analogue of re-apply-after-setData).
  if (STREAM_TILES_ENABLED) {
    map.on("sourcedata", (e) => {
      if (e.sourceId === "clickable-streams" && e.isSourceLoaded) {
        refreshLoadedFromTiles();
      }
    });
  }
  map.on("click", "clickable-streams-hit", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    onStreamClick((f.properties || {}) as ClickableStreamProps, e.lngLat);
  });
  map.on("mouseenter", "clickable-streams-hit", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "clickable-streams-hit", () => {
    map.getCanvas().style.cursor = "";
  });
});

export function setStreamsVisible(on: boolean): void {
  _streamsVisible = on;
  for (const id of ["clickable-streams", "clickable-streams-hit"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", visStr(on));
  }
}

// -- Viewport fetch ---------------------------------------------------

let _streamReqId = 0;
let _loadedClkNames: Set<string> = new Set();
let _loadedClkLpids: Set<number> = new Set();

/** Tile mode: MapLibre already fetches/decodes tiles, so there's nothing to
 *  fetch. Rebuild the loaded-reach name/lpid sets from the rendered tile
 *  features (drives gauge-dot suppression in renderRivers) and re-apply the
 *  active highlight. Called on moveend (via controls) + on tile arrival. */
function refreshLoadedFromTiles(): void {
  if (!map.getLayer("clickable-streams")) return;
  const feats = map.queryRenderedFeatures({ layers: ["clickable-streams"] });
  _loadedClkNames = new Set();
  _loadedClkLpids = new Set();
  for (const f of feats) {
    const p = (f.properties || {}) as ClickableStreamProps;
    if (p.gnis_name) _loadedClkNames.add(String(p.gnis_name).trim().toLowerCase());
    if (p.levelpathid != null) _loadedClkLpids.add(Number(p.levelpathid));
  }
  if (_selStreamKey != null) _applyHighlight(_selStreamKey);
  if (window.renderRivers) window.renderRivers();
}

export async function loadClickableStreams(): Promise<void> {
  if (!_streamsVisible) return; // toggled off
  if (STREAM_TILES_ENABLED) {
    refreshLoadedFromTiles();
    return;
  }
  if (map.getZoom() < STREAM_MIN_ZOOM) return;
  const b = map.getBounds();
  if (b.getEast() - b.getWest() > 4 || b.getNorth() - b.getSouth() > 4) return;
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((x) => x.toFixed(4))
    .join(",");
  const reqId = ++_streamReqId;
  try {
    const fc: GeoJsonFeatureCollection<ClickableStreamProps> = await fetch(
      `/api/clickable_streams?bbox=${bbox}&zoom=${map.getZoom()}`,
    ).then((r) => r.json());
    if (reqId !== _streamReqId) return; // a newer move superseded us
    getGeoJSON("clickable-streams")?.setData(fc);
    if (_selStreamKey != null) _applyHighlight(_selStreamKey);
    _loadedClkNames = new Set();
    _loadedClkLpids = new Set();
    for (const feat of fc.features || []) {
      const p = feat.properties || ({} as ClickableStreamProps);
      if (p.gnis_name) _loadedClkNames.add(p.gnis_name.trim().toLowerCase());
      if (p.levelpathid != null) _loadedClkLpids.add(p.levelpathid);
    }
    if (window.renderRivers) window.renderRivers();
  } catch (_) {
    /* transient; next moveend retries */
  }
}

export function _riverHasClickableReach(r: River): boolean {
  if (!_streamsVisible) return false;
  if (r.name && _loadedClkNames.has(r.name.trim().toLowerCase())) return true;
  if (Array.isArray(r.levelpathids)) {
    for (const lpid of r.levelpathids) {
      if (_loadedClkLpids.has(lpid)) return true;
    }
  }
  return false;
}

export function restyleStreams(): void {
  if (map.getLayer("clickable-streams")) {
    map.setPaintProperty("clickable-streams", "line-color", colorExpr());
  }
}

// -- Highlight (feature-state) ----------------------------------------
// Highlight every loaded reach sharing the clicked stream's named-river
// identity (or levelpathid for unnamed reaches). Re-applied after each
// setData so the selection persists + extends across pans.

interface SelStreamKey {
  name: string | null;
  lpid: number | null;
}

let _selStreamKey: SelStreamKey | null = null;

function _normName(s: string | null | undefined): string {
  return (s || "").trim().toLowerCase();
}

function _featMatchesKey(p: ClickableStreamProps, key: SelStreamKey): boolean {
  if (key.name) return _normName(p.gnis_name) === key.name;
  return p.levelpathid === key.lpid;
}

function _applyHighlight(key: SelStreamKey): void {
  const src = "clickable-streams";
  if (!map.getSource(src)) return;
  // Vector (tile) sources require a sourceLayer for querySourceFeatures +
  // feature-state; GeoJSON sources ignore it.
  const feats = STREAM_TILES_ENABLED
    ? map.querySourceFeatures(src, { sourceLayer: STREAM_SOURCE_LAYER })
    : map.querySourceFeatures(src);
  for (const f of feats) {
    if (f.id == null) continue;
    if (_featMatchesKey((f.properties || {}) as ClickableStreamProps, key)) {
      map.setFeatureState(
        STREAM_TILES_ENABLED
          ? { source: src, sourceLayer: STREAM_SOURCE_LAYER, id: f.id }
          : { source: src, id: f.id },
        { selected: true },
      );
    }
  }
}

export function highlightStream(p: ClickableStreamProps): void {
  clearStreamHighlight();
  const name = _normName(p && p.gnis_name);
  _selStreamKey = {
    name: name || null,
    lpid: p && p.levelpathid != null ? p.levelpathid : null,
  };
  _applyHighlight(_selStreamKey);
}

export function clearStreamHighlight(): void {
  if (_selStreamKey == null) return;
  if (map.getSource("clickable-streams")) {
    map.removeFeatureState(
      STREAM_TILES_ENABLED
        ? { source: "clickable-streams", sourceLayer: STREAM_SOURCE_LAYER }
        : { source: "clickable-streams" },
    );
  }
  _selStreamKey = null;
}

// -- Click bridging: clickable reach -> gauged river ------------------

function _gaugedRiverFor(
  p: ClickableStreamProps,
  lngLat: maplibregl.LngLat | null,
): River | null {
  const name = _normName(p.gnis_name);
  const lpid = p.levelpathid;
  if (!name && lpid == null) return null;
  const seen = new Set<string>();
  const matches: River[] = [];
  for (const list of [window.allRivers || [], window.stateRivers || []]) {
    if (!list) continue;
    for (const r of list) {
      if (!r.site_no || seen.has(r.site_no)) continue;
      const nameMatch = name && _normName(r.name) === name;
      const lpidMatch =
        lpid != null &&
        Array.isArray(r.levelpathids) &&
        r.levelpathids.includes(lpid);
      if (nameMatch || lpidMatch) {
        seen.add(r.site_no);
        matches.push(r);
      }
    }
  }
  if (matches.length <= 1 || !lngLat) return matches[0] || null;
  let best = matches[0];
  let bestD = Infinity;
  for (const r of matches) {
    const dy = r.lat - lngLat.lat;
    const dx = r.lon - lngLat.lng;
    const d = dy * dy + dx * dx;
    if (d < bestD) {
      bestD = d;
      best = r;
    }
  }
  return best;
}

// -- Click handler ----------------------------------------------------

export function onStreamClick(
  p: ClickableStreamProps,
  lngLat: maplibregl.LngLat | null,
): void {
  highlightStream(p);
  const gauged = _gaugedRiverFor(p, lngLat);
  if (gauged) {
    // Also highlight the gauged river's flowline (drawn above clickable-streams),
    // so the selection reads red even where the flowline would mask it.
    openRiverPanel(
      gauged,
      gauged.site_no ? { source: "river-lines", id: gauged.site_no } : null,
    );
    return;
  }
  const got = prepareRiverPanel();
  if (!got) return;
  const { panel, body } = got;
  const cls = p.trout_class;
  const label = (cls && STREAM_CLASS_LABEL[String(cls)]) || "No trout designation";
  body.innerHTML =
    `<div class="bl-card"><div class="bl-card-head">` +
    `<div style="font-size:18px;font-weight:700;color:#1a1a2e">${esc(p.gnis_name || "Unnamed stream")}</div>` +
    `<span class="stream-badge" style="background:${esc(streamColor(p))}">${esc(label)}</span>` +
    `<span class="stream-badge" style="background:#64748b">Order ${esc(p.streamorder)}</span>` +
    `<div class="bl-summary">Ungauged reach &mdash; no live USGS flow here. Showing what we know.</div>` +
    `</div><div class="bl-card-body">` +
    `<div class="bl-catch-cta"></div>` +
    `<details class="bl-section bl-hatch"><summary>Hatching now</summary>` +
    `<div class="bl-section-body">Hatch guidance for this reach's zone lands with the ungauged-card data wire-up.</div></details>` +
    `<details class="bl-section" open><summary>Trout</summary>` +
    `<div class="bl-section-body">${esc(label)}${cls ? " (state designation)" : ""}.</div></details>` +
    `<details class="bl-section"><summary>Access &amp; land</summary>` +
    `<div class="bl-section-body">Access points + public/private land coming in the TroutRoutes-depth phase.</div></details>` +
    `<details class="bl-section"><summary>Conditions</summary>` +
    `<div class="bl-section-body">No gauge on this reach. Nearest downstream gauge could provide context.</div></details>` +
    `</div></div>`;
  commitRiverPanelOpen(panel, body, "open");
  if (window.wireCatch) {
    window.wireCatch(body, {
      name: p.gnis_name,
      site_no: null,
      lat: lngLat ? lngLat.lat : null,
      lon: lngLat ? lngLat.lng : null,
    });
  }
}

// -- Window bridge ----------------------------------------------------

declare global {
  interface Window {
    streamColorMode: "trout" | "conditions";
    setStreamColorMode: typeof setStreamColorMode;
    streamColor: typeof streamColor;
    restyleStreams: typeof restyleStreams;
    loadClickableStreams: typeof loadClickableStreams;
    _riverHasClickableReach: typeof _riverHasClickableReach;
    highlightStream: typeof highlightStream;
    clearStreamHighlight: typeof clearStreamHighlight;
    onStreamClick: typeof onStreamClick;
  }
}

window.streamColorMode = streamColorMode;
window.setStreamColorMode = setStreamColorMode;
window.streamColor = streamColor;
window.restyleStreams = restyleStreams;
window.loadClickableStreams = loadClickableStreams;
window._riverHasClickableReach = _riverHasClickableReach;
window.highlightStream = highlightStream;
window.clearStreamHighlight = clearStreamHighlight;
window.onStreamClick = onStreamClick;
