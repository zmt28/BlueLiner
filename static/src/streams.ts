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
 *   - Color is a data-driven `match` on trout_class, collapsed into three
 *     semantic buckets (wild / stocked-managed / unclassified). The active
 *     "Map Style" (wild / stocked / all) chooses which buckets are emphasized
 *     vs faded -- swapped at runtime via setPaintProperty, no refetch. Width
 *     is an `interpolate` on streamorder.
 *   - Selection highlight is feature-state ("selected"); re-applied
 *     after each setData so it persists/extends as the user pans, and it
 *     survives a style swap (feature-state is independent of paint).
 */

import maplibregl, { ExpressionSpecification, LayerSpecification } from "maplibre-gl";
import { map, onMapReady } from "./map-setup";
import { esc } from "./util";
import { STREAM_TILES_ENABLED, STREAM_TILES_URL, STREAM_SOURCE_LAYER } from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import { prepareRiverPanel, commitRiverPanelOpen } from "./river-panel";
import { selectRiver } from "./selection";

// -- Stream tier coloring (the nationwide quality axis) --------------
// Tiles carry `tier` (gold/class1/class2/class3 or null), normalized in the
// build from the per-state designations via trout_registry so it means the
// same thing nationwide. We color by tier; the raw `trout_class` is kept only
// to name the exact agency designation on the reach card. Wild/native are
// separate filter flags (is_wild / is_native), not part of the color.

const TIER_COLOR: Record<StreamTier, string> = {
  gold: "#d4af37", // gold -- premier / blue-ribbon water
  class1: "#1e8449", // green -- high-quality
  class2: "#2e86c1", // blue -- solid everyday trout water
  class3: "#7f8c9a", // slate -- lighter / put-and-take
  unclassified: "#8a9bb0", // grey -- network reach with no trout tier
};

/** Faint color for reaches with no tier. Recedes via low opacity too. */
const FAINT = TIER_COLOR.unclassified;

// Verdict colors for the selected river's line highlight. MapLibre paint
// needs literal hexes, so these mirror the --bl-cond-*-500 tokens in
// tokens.css -- the same palette as the gauge condition discs.
const VERDICT_COLOR: Record<ConditionKey, string> = {
  green: "#4A8C5C", // --bl-cond-good-500 (moss)
  yellow: "#B7892F", // --bl-cond-fair-500 (ochre)
  red: "#B3473B", // --bl-cond-poor-500 (clay)
  gray: "#7F8B9C", // --bl-cond-none-500 (stone)
};

/** Selection color for ungauged reaches (no verdict feature-state). */
const SELECTED_FALLBACK = "#e74c3c";

export const TIER_LABEL: Record<StreamTier, string> = {
  gold: "Gold — premier water",
  class1: "Class 1 — high quality",
  class2: "Class 2 — solid trout water",
  class3: "Class 3 — lighter / stocked",
  unclassified: "Unclassified",
};

/** Full per-state designation labels, kept for the (detail-rich) reach card --
 *  the map shows the nationwide tier, but the card names the exact agency
 *  designation. */
export const STREAM_CLASS_LABEL: Record<string, string> = {
  class_a: "Class A wild trout",
  wilderness: "Wilderness trout",
  wild_reproduction: "Wild reproduction",
  stocked: "Stocked trout",
  designated: "Designated trout",
};

export function streamTier(p: ClickableStreamProps): StreamTier {
  const t = p.tier;
  return t && t in TIER_COLOR ? (t as StreamTier) : "unclassified";
}

let _streamsVisible = true; // lyr-fishable default checked

/** Tier color, used by the reach-card badge. */
export function streamColor(p: ClickableStreamProps): string {
  return TIER_COLOR[streamTier(p)];
}

// -- Stream filters (wild / native) ---------------------------------
// Two orthogonal toggles layered over the tier coloring: show only reaches
// with naturally-reproducing wild trout, and/or native species. Persisted to
// localStorage; applied via setFilter on both stream layers (no refetch).

function loadStreamFilters(): StreamFilters {
  try {
    return {
      wild: localStorage.getItem("bl_filter_wild") === "1",
      native: localStorage.getItem("bl_filter_native") === "1",
    };
  } catch (_) {
    return { wild: false, native: false };
  }
}

let _filters: StreamFilters = loadStreamFilters();

export function currentStreamFilters(): StreamFilters {
  return { ..._filters };
}

/** MapLibre filter for the active wild/native toggles (AND), or null = all. */
function streamFilterExpr(): unknown[] | null {
  const clauses: unknown[] = [];
  if (_filters.wild) clauses.push(["==", ["get", "is_wild"], true]);
  if (_filters.native) clauses.push(["==", ["get", "is_native"], true]);
  return clauses.length ? ["all", ...clauses] : null;
}

function applyStreamFilter(): void {
  const expr = streamFilterExpr();
  for (const id of ["clickable-streams", "clickable-streams-hit"]) {
    if (map.getLayer(id)) map.setFilter(id, expr as never);
  }
}

export function setStreamFilters(next: Partial<StreamFilters>): void {
  _filters = { ..._filters, ...next };
  try {
    localStorage.setItem("bl_filter_wild", _filters.wild ? "1" : "0");
    localStorage.setItem("bl_filter_native", _filters.native ? "1" : "0");
  } catch (_) {
    /* localStorage unavailable; in-memory state still reflects */
  }
  applyStreamFilter();
}

// -- Paint expressions ------------------------------------------------
// Color + opacity are a static `match` on the nationwide `tier`; the wild/
// native toggles are a layer FILTER (applyStreamFilter), not a paint swap.

const TIER_COLOR_MATCH: ExpressionSpecification = [
  "match",
  ["get", "tier"],
  "gold", TIER_COLOR.gold,
  "class1", TIER_COLOR.class1,
  "class2", TIER_COLOR.class2,
  "class3", TIER_COLOR.class3,
  FAINT, // no tier -- faint grey
] as unknown as ExpressionSpecification;

const TIER_OPACITY_MATCH: ExpressionSpecification = [
  "match",
  ["get", "tier"],
  "gold", 0.9,
  "class1", 0.85,
  "class2", 0.8,
  "class3", 0.7,
  0.35, // no tier -- receded
] as unknown as ExpressionSpecification;

function colorExpr(): ExpressionSpecification {
  // Selected: verdict-colored when the river is gauged (the `verdict`
  // feature-state carries conditions.overall), flat red otherwise. The
  // "selected" signal itself stays width-8 + opacity (WIDTH_EXPR).
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    [
      "match",
      ["coalesce", ["feature-state", "verdict"], ""],
      "green", VERDICT_COLOR.green,
      "yellow", VERDICT_COLOR.yellow,
      "red", VERDICT_COLOR.red,
      "gray", VERDICT_COLOR.gray,
      SELECTED_FALLBACK,
    ],
    TIER_COLOR_MATCH,
  ] as unknown as ExpressionSpecification;
}

function opacityExpr(): ExpressionSpecification {
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    0.95,
    TIER_OPACITY_MATCH,
  ] as unknown as ExpressionSpecification;
}

const WIDTH_EXPR: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  8,
  ["interpolate", ["linear"], ["coalesce", ["get", "streamorder"], 3], 1, 4, 7, 7],
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
      "line-opacity": opacityExpr(),
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
  // Apply the persisted wild/native filter to the freshly-added layers.
  applyStreamFilter();
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
// Overall verdict of the selected GAUGED river, carried into the
// `verdict` feature-state so the highlight paints in condition color.
// Null for ungauged-reach selections (colorExpr falls back to red).
let _selVerdict: ConditionKey | null = null;

function _cleanName(s: string | null | undefined): string | null {
  // The tiles can carry the literal string "nan" -- a pandas NaN that got
  // stringified during the build (unnamed NHD reaches). Treat it, and other
  // null-ish placeholders, as no name so unnamed reaches don't all collapse
  // into one "nan" river (which would highlight the whole region at once).
  const n = (s || "").trim();
  if (!n || n.toLowerCase() === "nan" || n.toLowerCase() === "none") return null;
  return n;
}

function _normName(s: string | null | undefined): string {
  return (_cleanName(s) || "").toLowerCase();
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
        _selVerdict ? { selected: true, verdict: _selVerdict } : { selected: true },
      );
    }
  }
}

export function highlightStream(
  p: ClickableStreamProps,
  verdict: ConditionKey | null = null,
): void {
  clearStreamHighlight();
  _selVerdict = verdict;
  const name = _normName(p && p.gnis_name);
  _selStreamKey = {
    name: name || null,
    lpid: p && p.levelpathid != null ? p.levelpathid : null,
  };
  _applyHighlight(_selStreamKey);
}

/** Update the verdict on the live highlight without re-keying it --
 *  fresh /api/rivers data can change the selected river's overall
 *  verdict (refreshSelectedRiver in selection.ts). */
export function setStreamHighlightVerdict(verdict: ConditionKey | null): void {
  if (verdict === _selVerdict) return;
  _selVerdict = verdict;
  if (_selStreamKey != null) _applyHighlight(_selStreamKey);
}

export function clearStreamHighlight(): void {
  _selVerdict = null;
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
  const gauged = _gaugedRiverFor(p, lngLat);
  if (gauged) {
    // Gauged river: central selection opens the panel and highlights by
    // the clicked reach's identity.
    selectRiver(gauged, p);
    return;
  }
  // Ungauged reach: highlight it, but it's a stream selection, not a
  // river selection (selection.ts stays null) -- the card below is all
  // this reach gets.
  highlightStream(p);
  const got = prepareRiverPanel();
  if (!got) return;
  const { panel, body } = got;
  const cls = p.trout_class;
  const label = (cls && STREAM_CLASS_LABEL[String(cls)]) || "No trout designation";
  const loading = '<div class="bl-reach-msg">Loading&hellip;</div>';
  body.innerHTML =
    `<div class="bl-card"><div class="bl-card-head">` +
    `<div style="font-size:18px;font-weight:700;color:#1a1a2e">${esc(_cleanName(p.gnis_name) || "Unnamed stream")}</div>` +
    `<span class="stream-badge" style="background:${esc(streamColor(p))}">${esc(label)}</span>` +
    `<span class="stream-badge" style="background:#64748b">Order ${esc(p.streamorder)}</span>` +
    `<div class="bl-summary">Ungauged reach &mdash; no live USGS flow here. Showing what we know.</div>` +
    `</div><div class="bl-card-body">` +
    `<div class="bl-catch-cta"></div>` +
    `<details class="bl-section bl-hatch" open><summary>Hatching now</summary>` +
    `<div class="bl-section-body" data-reach-sec="hatch">${loading}</div></details>` +
    `<details class="bl-section"><summary>Trout</summary>` +
    `<div class="bl-section-body">${esc(label)}${cls ? " (state designation)" : ""}.</div></details>` +
    `<details class="bl-section"><summary>Stocked nearby</summary>` +
    `<div class="bl-section-body" data-reach-sec="stocked">${loading}</div></details>` +
    `<details class="bl-section"><summary>Access</summary>` +
    `<div class="bl-section-body" data-reach-sec="access">${loading}</div></details>` +
    `<details class="bl-section"><summary>Conditions</summary>` +
    `<div class="bl-section-body">No USGS gauge on this reach &mdash; no live flow or temperature here. ` +
    `Tap a nearby gauged river for current conditions.</div></details>` +
    `</div></div>`;
  commitRiverPanelOpen(panel, body, "open");
  if (window.wireCatch) {
    window.wireCatch(body, {
      name: _cleanName(p.gnis_name),
      site_no: null,
      lat: lngLat ? lngLat.lat : null,
      lon: lngLat ? lngLat.lng : null,
    });
  }
  loadReachDetail(body, lngLat, _cleanName(p.gnis_name));
}

// -- Ungauged-card data (hatch / stocked / access) -------------------
// Filled async after the card opens so the panel appears instantly. A
// sequence guard drops a stale response if the user clicks another reach
// before this one returns.

let _reachSeq = 0;

function _fillReachSection(
  body: HTMLElement,
  sec: "hatch" | "stocked" | "access",
  html: string,
): void {
  const el = body.querySelector(`[data-reach-sec="${sec}"]`);
  if (el) el.innerHTML = html;
}

function _typeLabel(t: string | undefined): string {
  return (t || "access").replace(/_/g, " ");
}

function renderReachHatch(h: ReachDetail["hatch"]): string {
  const zone = h.zone ? ` &middot; ${esc(h.zone)}` : "";
  if (!h.active || !h.active.length) {
    return `<div class="bl-reach-msg">No major mayfly/caddis hatches indexed ` +
      `this month${zone} &mdash; fish midges, eggs, and streamers.</div>`;
  }
  const rows = h.active.map((e) => {
    const patterns = (e.patterns || []).join(", ");
    const meta = [e.hook_sizes ? `Hooks ${esc(e.hook_sizes)}` : "",
                  e.time_of_day ? esc(e.time_of_day) : ""]
      .filter(Boolean).join(" &middot; ");
    return `<div class="bl-reach-row">` +
      `<div class="bl-reach-row-title">${esc(e.common_name || "")}` +
      (e.insect ? ` <span class="bl-reach-sci">${esc(e.insect)}</span>` : "") +
      `</div>` +
      (meta ? `<div class="bl-reach-row-meta">${meta}</div>` : "") +
      (patterns ? `<div class="bl-reach-row-try">Try: ${esc(patterns)}</div>` : "") +
      `</div>`;
  }).join("");
  return `<div class="bl-reach-sub">Hatching now${zone}</div>${rows}`;
}

function renderReachStocked(list: ReachStockedEntry[]): string {
  if (!list.length) {
    return `<div class="bl-reach-msg">No stocked waters mapped within ~3&nbsp;km.</div>`;
  }
  return list.map((s) => {
    const species = (s.species || []).join(", ");
    const sub = [species, s.category ? esc(s.category) : ""]
      .filter(Boolean).join(" &middot; ");
    const link = s.agency_url
      ? ` <a href="${esc(s.agency_url)}" target="_blank" rel="noopener noreferrer">info &rarr;</a>`
      : "";
    return `<div class="bl-reach-row">` +
      `<div class="bl-reach-row-title">${esc(s.water || "Stocked water")}${link}</div>` +
      (sub ? `<div class="bl-reach-row-meta">${esc(species)}${species && s.category ? " &middot; " : ""}${esc(s.category || "")}</div>` : "") +
      `</div>`;
  }).join("");
}

function renderReachAccess(list: ReachAccessEntry[]): string {
  if (!list.length) {
    return `<div class="bl-reach-msg">No mapped access points within ~3&nbsp;km. ` +
      `Public/private land is coming in a later pass.</div>`;
  }
  return list.map((a) => {
    const meta = [_typeLabel(a.type), a.access ? esc(a.access) : ""]
      .filter(Boolean).join(" &middot; ");
    const notes = a.notes ? `<div class="bl-reach-row-meta">${esc(a.notes)}</div>` : "";
    const link = a.agency_url
      ? ` <a href="${esc(a.agency_url)}" target="_blank" rel="noopener noreferrer">info &rarr;</a>`
      : "";
    return `<div class="bl-reach-row">` +
      `<div class="bl-reach-row-title">${esc(a.name || "Access point")}${link}</div>` +
      `<div class="bl-reach-row-meta">${esc(meta)}</div>` +
      notes +
      `</div>`;
  }).join("");
}

async function loadReachDetail(
  body: HTMLElement,
  lngLat: maplibregl.LngLat | null,
  name: string | null,
): Promise<void> {
  const seq = ++_reachSeq;
  if (!lngLat) {
    const msg = `<div class="bl-reach-msg">Location unavailable.</div>`;
    for (const s of ["hatch", "stocked", "access"] as const) _fillReachSection(body, s, msg);
    return;
  }
  const q = new URLSearchParams({ lat: String(lngLat.lat), lon: String(lngLat.lng) });
  if (name) q.set("name", name);
  let data: ReachDetail | null = null;
  try {
    data = (await fetch(`/api/reach_detail?${q.toString()}`).then((r) => r.json())) as ReachDetail;
  } catch (_) {
    data = null;
  }
  if (seq !== _reachSeq) return; // superseded by a newer reach click
  if (!data) {
    const err = `<div class="bl-reach-msg">Couldn't load reach details.</div>`;
    for (const s of ["hatch", "stocked", "access"] as const) _fillReachSection(body, s, err);
    return;
  }
  _fillReachSection(body, "hatch", renderReachHatch(data.hatch));
  _fillReachSection(body, "stocked", renderReachStocked(data.stocked || []));
  _fillReachSection(body, "access", renderReachAccess(data.access || []));
}

// -- Window bridge ----------------------------------------------------

declare global {
  interface Window {
    streamColor: typeof streamColor;
    loadClickableStreams: typeof loadClickableStreams;
    highlightStream: typeof highlightStream;
    setStreamHighlightVerdict: typeof setStreamHighlightVerdict;
    clearStreamHighlight: typeof clearStreamHighlight;
    onStreamClick: typeof onStreamClick;
  }
}

window.streamColor = streamColor;
window.loadClickableStreams = loadClickableStreams;
window.highlightStream = highlightStream;
window.setStreamHighlightVerdict = setStreamHighlightVerdict;
window.clearStreamHighlight = clearStreamHighlight;
window.onStreamClick = onStreamClick;
