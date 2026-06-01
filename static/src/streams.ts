/**
 * Clickable-stream network on MapLibre GL JS. The NHDPlus reach layer users
 * click to open the river panel (or the ungauged-stream card).
 *
 * The "clickable-streams" source is a VECTOR source backed by a static
 * PMTiles archive on R2 (read via the pmtiles:// protocol), configured by
 * VITE_STREAM_TILES_URL. The per-viewport GeoJSON path (/api/clickable_streams,
 * with its zoom gate + bbox cap) was retired in M3 — MapLibre fetches/decodes
 * the tiles itself.
 *
 * MapLibre specifics vs the old Leaflet version:
 *   - Two line layers off one source: a styled visible line and a
 *     transparent fat "hit" casing for touch. Clicks bind to the hit layer.
 *   - Color is a data-driven `match` on trout_class (streams are always
 *     trout-class colored now -- live conditions live on the per-gauge
 *     icons); width an `interpolate` on streamorder.
 *   - Selection highlight is feature-state ("selected"); re-applied
 *     after each setData so it persists/extends as the user pans.
 */

import maplibregl, { ExpressionSpecification, LayerSpecification } from "maplibre-gl";
import { map, onMapReady } from "./map-setup";
import { esc } from "./util";
import { STREAM_TILES_ENABLED, STREAM_TILES_URL, STREAM_SOURCE_LAYER } from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import {
  prepareRiverPanel,
  commitRiverPanelOpen,
  openRiverPanel,
} from "./river-panel";

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

let _streamsVisible = true; // lyr-fishable default checked

/** Per-class color, used by the ungauged-card badge. Streams are always
 *  colored by trout class (the trout/conditions toggle was removed -- live
 *  conditions live on the per-gauge icons now). */
export function streamColor(p: ClickableStreamProps): string {
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
  // Always trout-class colored; red when selected.
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    "#e74c3c",
    TROUT_COLOR_MATCH,
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

const SRC_LAYER = { "source-layer": STREAM_SOURCE_LAYER };

onMapReady(() => {
  // The GeoJSON fallback was retired in M3 — the clickable network is now
  // served only as static PMTiles on R2 (read via the pmtiles:// protocol;
  // HTTP range requests straight to the CDN). Streams require the tile URL.
  if (!STREAM_TILES_ENABLED) return;
  ensurePmtilesProtocol();
  map.addSource("clickable-streams", {
    type: "vector",
    url: `pmtiles://${STREAM_TILES_URL}`,
    promoteId: "levelpathid",
  });
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
  // Re-apply the selection highlight as new tiles arrive (pan/zoom).
  map.on("sourcedata", (e) => {
    if (e.sourceId === "clickable-streams" && e.isSourceLoaded) {
      reapplyStreamHighlight();
    }
  });
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

// -- Highlight re-apply on tile load ----------------------------------
// New tiles arriving (pan/zoom) don't carry the prior feature-state, so the
// selected river's reaches must be re-highlighted as they load.

export function reapplyStreamHighlight(): void {
  if (!_streamsVisible) return;
  if (_selStreamKey != null) _applyHighlight(_selStreamKey);
}

/** Kept as the moveend hook name (controls.ts) -- re-applies the highlight
 *  once the viewport settles. (Gauge dots are no longer suppressed by the
 *  clickable network, so there's nothing else to refresh here.) */
export function loadClickableStreams(): void {
  reapplyStreamHighlight();
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
  // feature-state.
  const feats = map.querySourceFeatures(src, { sourceLayer: STREAM_SOURCE_LAYER });
  for (const f of feats) {
    if (f.id == null) continue;
    if (_featMatchesKey((f.properties || {}) as ClickableStreamProps, key)) {
      map.setFeatureState(
        { source: src, sourceLayer: STREAM_SOURCE_LAYER, id: f.id },
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
    map.removeFeatureState({
      source: "clickable-streams",
      sourceLayer: STREAM_SOURCE_LAYER,
    });
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
    // The clicked reach already carries the red selection highlight
    // (highlightStream above); the gauged river's panel just opens.
    openRiverPanel(gauged);
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
    streamColor: typeof streamColor;
    loadClickableStreams: typeof loadClickableStreams;
    highlightStream: typeof highlightStream;
    clearStreamHighlight: typeof clearStreamHighlight;
    onStreamClick: typeof onStreamClick;
  }
}

window.streamColor = streamColor;
window.loadClickableStreams = loadClickableStreams;
window.highlightStream = highlightStream;
window.clearStreamHighlight = clearStreamHighlight;
window.onStreamClick = onStreamClick;
