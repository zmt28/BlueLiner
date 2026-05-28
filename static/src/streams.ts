/**
 * Clickable-stream network: the per-viewport NHDPlus reach layer that
 * users click to open the river panel (or the generic ungauged-stream
 * card for reaches that don't belong to a gauged river). Extracted
 * from app.js in PR B1g -- largest single extraction in the series.
 *
 * Owns:
 *   - clickableVisible / clickableHit / clickableLayer  the stacked
 *     two-layer pattern (thin styled visible line + transparent fat
 *     hit casing for touch targets)
 *   - stream-style helpers: streamColor, streamWeight, streamStyle,
 *     STREAM_CLASS_COLORS, STREAM_CLASS_LABEL, STREAM_MIN_ZOOM
 *   - streamColorMode + restyleStreams (the trout-class vs conditions
 *     coloring toggle)
 *   - loadClickableStreams: bbox-bound, debounced viewport fetch
 *   - _loadedClkNames / _loadedClkLpids (gnis_name + levelpathid
 *     sets used by _riverHasClickableReach to suppress redundant
 *     gauge-dot markers when the same river is already drawn as a
 *     clickable line)
 *   - the highlight state machine (_selStreamKey + _paintHighlight +
 *     _featMatchesKey + highlightStream + clearStreamHighlight)
 *   - _gaugedRiverFor: matches a clicked reach to a loaded gauged
 *     river by name OR NHD levelpath
 *   - onStreamClick: the click handler that either opens the river
 *     panel (gauged) or builds the ungauged card inline
 *
 * Cross-module dependencies:
 *   - map (map-setup)
 *   - esc (util)
 *   - prepareRiverPanel, commitRiverPanelOpen, openRiverPanel (river-panel)
 *   - allRivers / stateRivers: still owned by app.js (rivers code,
 *     PR B1h). Read via `window.allRivers` / `window.stateRivers`
 *     LAZILY inside _gaugedRiverFor so reassignments in app.js are
 *     visible. (Capturing into a module-level const at the top
 *     would freeze on the initial empty arrays.)
 *   - wireCatch: still in app.js (PR B1j). Read via window inside
 *     the ungauged-card path.
 *
 * Window-bridged so app.js's renderRivers + controls-panel wiring
 * + moveend debouncer + the wireLayerToggle for "lyr-fishable" all
 * keep working.
 */

import * as L from "leaflet";
import { map } from "./map-setup";
import { esc } from "./util";
import {
  prepareRiverPanel,
  commitRiverPanelOpen,
  openRiverPanel,
} from "./river-panel";

// -- Style constants --------------------------------------------------

// Below this zoom the viewport is too large to draw the network
// sensibly (and the API would return tens of thousands of reaches).
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

// "trout" colors by class; "conditions" greys the network so the
// gauged condition colors (green/yellow/red) read on top (Decision C).
let streamColorMode: "trout" | "conditions" = "trout";

export function setStreamColorMode(mode: "trout" | "conditions"): void {
  streamColorMode = mode;
  window.streamColorMode = mode;
}

export function streamColor(p: ClickableStreamProps): string {
  if (streamColorMode === "conditions") return "#9aa7b8";
  const cls = p.trout_class;
  return (cls && STREAM_CLASS_COLORS[String(cls)]) || "#8a9bb0";
}

function streamWeight(p: ClickableStreamProps): number {
  // Floor of 4px keeps even order-1 headwaters a tappable target (a
  // 2px line is nearly impossible to hit on touch). Scales up with
  // order.
  return Math.max(4, Math.min(7, (p.streamorder || 3) * 0.9));
}

export function streamStyle(p: ClickableStreamProps): L.PathOptions {
  return { color: streamColor(p), weight: streamWeight(p), opacity: 0.8 };
}

// -- Stacked clickable layers -----------------------------------------
// Two layers per feature: a thin styled visible line and a transparent
// fat "hit casing" on top to catch finger taps on mobile (a 4px line
// is still a poor touch target). Visible is non-interactive so clicks
// unambiguously go to the casing; both render the same FC.

export const clickableVisible: L.GeoJSON = L.geoJSON(null, {
  style: (f) => streamStyle((f?.properties || {}) as ClickableStreamProps),
  interactive: false,
});
const clickableHit: L.GeoJSON = L.geoJSON(null, {
  style: () => ({ color: "#000", weight: 16, opacity: 0, lineCap: "round" }),
  onEachFeature: (f, l) =>
    l.on("click", (e: L.LeafletMouseEvent) => {
      L.DomEvent.stop(e);
      onStreamClick(
        (f.properties || {}) as ClickableStreamProps,
        e.latlng,
      );
    }),
});

export const clickableLayer: L.FeatureGroup = L.featureGroup([
  clickableVisible,
  clickableHit,
]).addTo(map);

// -- Viewport fetch ---------------------------------------------------
// Names + levelpathids currently rendered in clickableVisible. The
// dot-suppression logic in app.js's renderRivers uses these to skip a
// gauge marker when the same river is already reachable as a
// clickable line -- avoids the "Antietam Creek dot on an unnamed
// tributary" surprise and the Susquehanna's redundant dot-over-line.
let _streamReqId = 0;
let _loadedClkNames: Set<string> = new Set();
let _loadedClkLpids: Set<number> = new Set();

export async function loadClickableStreams(): Promise<void> {
  if (!map.hasLayer(clickableLayer)) return; // toggled off
  if (map.getZoom() < STREAM_MIN_ZOOM) {
    // Earlier versions cleared the layer here. That made panning
    // across the zoom-9 boundary blink the entire stream network off
    // and back on. Now we just no-op; the previous frame's features
    // linger until the next moveend at zoom 9+ replaces them. The
    // bbox-area guard below + the per-zoom StreamOrder filter still
    // prevent country-scale fetches when the user is way zoomed out.
    return;
  }
  const b = map.getBounds();
  // 4° cap (was 6°): at zoom 9 a 6° bbox is already country-scale on
  // tall screens and a fast pinch-out can briefly cross the guard,
  // firing a fetch that returns tens of thousands of features and
  // locks the main thread.
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
    clickableVisible.clearLayers();
    clickableHit.clearLayers();
    clickableVisible.addData(fc);
    clickableHit.addData(fc);
    if (_selStreamKey != null) _paintHighlight(_selStreamKey);
    _loadedClkNames = new Set();
    _loadedClkLpids = new Set();
    for (const feat of fc.features || []) {
      const p = feat.properties || ({} as ClickableStreamProps);
      if (p.gnis_name) _loadedClkNames.add(p.gnis_name.trim().toLowerCase());
      if (p.levelpathid != null) _loadedClkLpids.add(p.levelpathid);
    }
    // renderRivers (in app.js) uses _riverHasClickableReach to decide
    // dot vs line per river; re-render after a new fetch so its
    // decisions reflect the freshly-loaded reach set.
    if (window.renderRivers) window.renderRivers();
  } catch (_) {
    /* transient; next moveend retries */
  }
}

/**
 * True when this river has at least one reach currently drawn in the
 * clickable network -- if so, the user can already click the line to
 * open the river panel, so a redundant gauge dot adds noise (and,
 * for rivers like Antietam Creek, lands the dot on an unrelated
 * tributary at the gauge-centroid). Falls back to false when the
 * clickable layer is off or zoomed below STREAM_MIN_ZOOM -- the dot
 * is the only access point then.
 */
export function _riverHasClickableReach(r: River): boolean {
  if (!map.hasLayer(clickableLayer)) return false;
  if (r.name && _loadedClkNames.has(r.name.trim().toLowerCase())) return true;
  if (Array.isArray(r.levelpathids)) {
    for (const lpid of r.levelpathids) {
      if (_loadedClkLpids.has(lpid)) return true;
    }
  }
  return false;
}

export function restyleStreams(): void {
  clickableVisible.setStyle((f) =>
    streamStyle((f?.properties || {}) as ClickableStreamProps),
  );
  if (_selStreamKey != null) _paintHighlight(_selStreamKey);
}

// -- Highlight state machine ------------------------------------------
// Highlight every rendered reach sharing the clicked stream's *named-
// river* identity (or LevelPathID for unnamed reaches). NHDPlusV2
// frequently splits a single named river across multiple levelpathids
// at HUC boundaries, so matching on lpid alone leaves the upper and
// lower reaches of e.g. "Little Conestoga Creek" unhighlighted; the
// composite key falls back to lpid only when the clicked reach has no
// gnis_name (true for ~1/3 of order-1 headwaters). `_paintHighlight`
// is idempotent so it can be re-applied after the clickable-streams
// layer is rebuilt (pan/zoom moveend) or restyled (trout/conditions
// mode toggle) -- without it, the red selection silently reverts to
// the base style as soon as you pan the map. The repaint-after-fetch
// path is what makes the highlight continue onto newly-loaded
// segments of the same named river as the user pans along its reach.

interface SelStreamKey {
  name: string | null;
  lpid: number | null;
}

let _selStreamKey: SelStreamKey | null = null;

function _normName(s: string | null | undefined): string {
  return (s || "").trim().toLowerCase();
}

function _featMatchesKey(
  f: { properties: ClickableStreamProps } | undefined,
  key: SelStreamKey,
): boolean {
  if (!f || !key) return false;
  if (key.name)
    return _normName(f.properties.gnis_name) === key.name;
  return f.properties.levelpathid === key.lpid;
}

function _paintHighlight(key: SelStreamKey): void {
  clickableVisible.eachLayer((l) => {
    const feat = (l as L.GeoJSON & { feature?: { properties: ClickableStreamProps } })
      .feature;
    if (feat && _featMatchesKey(feat, key)) {
      (l as L.Path).setStyle({ weight: 8, color: "#e74c3c", opacity: 0.95 });
    }
  });
}

export function highlightStream(p: ClickableStreamProps): void {
  clearStreamHighlight();
  const name = _normName(p && p.gnis_name);
  _selStreamKey = {
    name: name || null,
    lpid: p && p.levelpathid != null ? p.levelpathid : null,
  };
  _paintHighlight(_selStreamKey);
}

export function clearStreamHighlight(): void {
  if (_selStreamKey == null) return;
  clickableVisible.eachLayer((l) => {
    const feat = (l as L.GeoJSON & { feature?: { properties: ClickableStreamProps } })
      .feature;
    if (feat) {
      (l as L.Path).setStyle(streamStyle(feat.properties));
    }
  });
  _selStreamKey = null;
}

// -- Click bridging: clickable reach -> gauged river ------------------
// A clickable-network reach belongs to a gauged river when a loaded
// river shares either its GNIS name OR its NHD levelpath. Levelpath
// matching catches reaches that NHD and NLDI label differently for
// the same physical river (e.g., where NHD names a downstream tidal
// section "Gunpowder River" and an upstream section "Gunpowder Falls"
// on the same levelpath). When several rivers match, pick the one
// whose representative point is nearest the click.

function _gaugedRiverFor(
  p: ClickableStreamProps,
  latlng: L.LatLng | null,
): River | null {
  const name = _normName(p.gnis_name);
  const lpid = p.levelpathid;
  if (!name && lpid == null) return null;
  // Search BOTH the current set (viewport rivers when zoomed in) and
  // the full state snapshot, deduped by site_no. Without the
  // stateRivers fallback, clicking an upstream reach of a river whose
  // gauges sit outside the current bbox (e.g., the Gunpowder above
  // Glencoe) wouldn't find a match and would wrongly render as
  // ungauged.
  // NB: allRivers + stateRivers are still owned by app.js (rivers
  // code, B1h scope). Read LAZILY via window so reassignments are
  // visible (capturing into a module const at the top would freeze
  // on the initial empty arrays).
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
  if (matches.length <= 1 || !latlng) return matches[0] || null;
  let best = matches[0];
  let bestD = Infinity;
  for (const r of matches) {
    const dy = r.lat - latlng.lat;
    const dx = r.lon - latlng.lng;
    const d = dy * dy + dx * dx;
    if (d < bestD) {
      bestD = d;
      best = r;
    }
  }
  return best;
}

// -- The click handler itself -----------------------------------------

export function onStreamClick(
  p: ClickableStreamProps,
  latlng: L.LatLng | null,
): void {
  highlightStream(p); // whole-river emphasis, gauged or not
  // Unify the two layers: if this reach is part of a gauged river,
  // open that river's rich panel instead of the generic ungauged
  // card, so the whole river behaves as one thing regardless of
  // where you click.
  const gauged = _gaugedRiverFor(p, latlng);
  if (gauged) {
    openRiverPanel(gauged, null, null);
    return;
  }
  const got = prepareRiverPanel();
  if (!got) return;
  const { panel, body } = got;
  const cls = p.trout_class;
  const label =
    (cls && STREAM_CLASS_LABEL[String(cls)]) || "No trout designation";
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
  // Catch CTA: the clicked point is a reasonable catch location for
  // an ungauged stream (no representative gauge to attach to).
  // wireCatch still lives in app.js (B1j scope); reach via window.
  if (window.wireCatch) {
    window.wireCatch(body, {
      name: p.gnis_name,
      site_no: null,
      lat: latlng ? latlng.lat : null,
      lon: latlng ? latlng.lng : null,
    });
  }
}

// -- Window bridge for legacy app.js ----------------------------------

declare global {
  interface Window {
    clickableLayer: L.FeatureGroup;
    clickableVisible: L.GeoJSON;
    streamColorMode: "trout" | "conditions";
    setStreamColorMode: typeof setStreamColorMode;
    streamColor: typeof streamColor;
    streamStyle: typeof streamStyle;
    restyleStreams: typeof restyleStreams;
    loadClickableStreams: typeof loadClickableStreams;
    _riverHasClickableReach: typeof _riverHasClickableReach;
    highlightStream: typeof highlightStream;
    clearStreamHighlight: typeof clearStreamHighlight;
    onStreamClick: typeof onStreamClick;
    // allRivers / stateRivers / renderRivers are owned by rivers.ts
    // (PR B1h) and declared canonically there.
  }
}

window.clickableLayer = clickableLayer;
window.clickableVisible = clickableVisible;
window.streamColorMode = streamColorMode;
window.setStreamColorMode = setStreamColorMode;
window.streamColor = streamColor;
window.streamStyle = streamStyle;
window.restyleStreams = restyleStreams;
window.loadClickableStreams = loadClickableStreams;
window._riverHasClickableReach = _riverHasClickableReach;
window.highlightStream = highlightStream;
window.clearStreamHighlight = clearStreamHighlight;
window.onStreamClick = onStreamClick;
