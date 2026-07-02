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
import { map, onMapReady, lineOverlayAnchor } from "./map-setup";
import { esc } from "./util";
import { STREAM_TILES_ENABLED, STREAM_TILES_URL, STREAM_SOURCE_LAYER } from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import {
  prepareRiverPanel,
  commitRiverPanelOpen,
  panPointClearOfPanel,
} from "./river-panel";
import { selectRiver } from "./selection";
import { refreshIcons } from "./util";
import { autoLoadElevation } from "./elevation-profile";
import { mapClicksClaimed } from "./map-mode";

// -- Stream tier coloring (the nationwide quality axis) --------------
// Tiles carry `tier` (gold/class1/class2/class3 or null), normalized in the
// build from the per-state designations via trout_registry so it means the
// same thing nationwide. We color by tier; the raw `trout_class` is kept only
// to name the exact agency designation on the reach card. Wild/native are
// separate filter flags (is_wild / is_native), not part of the color.

export const TIER_COLOR: Record<StreamTier, string> = {
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

/** Line opacity for non-matching / ungauged reaches while a condition
 *  overlay is active. 0.15 keeps the network faintly traceable on the
 *  light street/topo basemaps without competing on dark satellite
 *  imagery (there is no CSS dark mode; satellite is the dark theme). */
const COND_FADE_OPACITY = 0.15;

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

// -- Stream filters (class + wild / native) -------------------------
// Three filters layered over the tier coloring, AND-ed: which quality classes
// to show (Gold / Class 1-3 / Unclassified), plus narrow to naturally-
// reproducing wild trout and/or native species. Persisted to localStorage;
// applied via setFilter on both stream layers (no refetch).

const ALL_TIERS: StreamTier[] = ["gold", "class1", "class2", "class3", "unclassified"];

function loadStreamFilters(): StreamFilters {
  const tiers = {} as Record<StreamTier, boolean>;
  let saved: string[] | null = null;
  let wild = false;
  let native = false;
  try {
    const raw = localStorage.getItem("bl_filter_tiers");
    if (raw != null) saved = raw.split(",").filter(Boolean);
    wild = localStorage.getItem("bl_filter_wild") === "1";
    native = localStorage.getItem("bl_filter_native") === "1";
  } catch (_) {
    /* localStorage unavailable -> defaults */
  }
  for (const t of ALL_TIERS) tiers[t] = saved ? saved.includes(t) : true;
  return { wild, native, tiers };
}

let _filters: StreamFilters = loadStreamFilters();

export function currentStreamFilters(): StreamFilters {
  return { ..._filters, tiers: { ..._filters.tiers } };
}

/** Class-selection clause for the layer filter; null when all classes are on. */
function tierClause(): unknown[] | null {
  const on = ALL_TIERS.filter((t) => _filters.tiers[t]);
  if (on.length === ALL_TIERS.length) return null; // all on -> no constraint
  if (on.length === 0) return ["==", ["literal", 1], ["literal", 0]]; // none -> hide all
  const named = on.filter((t) => t !== "unclassified");
  const clauses: unknown[] = [];
  if (named.length) clauses.push(["in", ["get", "tier"], ["literal", named]]);
  // "Unclassified" = a reach whose tier isn't one of the four named tiers.
  if (_filters.tiers.unclassified) {
    clauses.push([
      "!",
      ["in", ["get", "tier"], ["literal", ["gold", "class1", "class2", "class3"]]],
    ]);
  }
  return clauses.length === 1 ? (clauses[0] as unknown[]) : ["any", ...clauses];
}

/** MapLibre filter for the active class + wild/native filters (AND), or null. */
function streamFilterExpr(): unknown[] | null {
  const clauses: unknown[] = [];
  const tc = tierClause();
  if (tc) clauses.push(tc);
  if (_filters.wild) clauses.push(["==", ["get", "is_wild"], true]);
  if (_filters.native) clauses.push(["==", ["get", "is_native"], true]);
  return clauses.length ? ["all", ...clauses] : null;
}

// Labels only make sense on named reaches; AND-ed with the active
// class/wild/native filter so filtered-out water isn't labeled.
const LABEL_BASE_FILTER: unknown[] = [
  "!=",
  ["coalesce", ["get", "gnis_name"], ""],
  "",
];

function labelFilterExpr(): unknown[] {
  const expr = streamFilterExpr();
  return expr ? ["all", LABEL_BASE_FILTER, expr] : LABEL_BASE_FILTER;
}

function applyStreamFilter(): void {
  const expr = streamFilterExpr();
  for (const id of ["clickable-streams", "clickable-streams-hit"]) {
    if (map.getLayer(id)) map.setFilter(id, expr as never);
  }
  if (map.getLayer("stream-labels")) {
    map.setFilter("stream-labels", labelFilterExpr() as never);
  }
}

// -- Empty-filter feedback (M2.h6) -------------------------------------
// When the class/wild/native filters leave nothing in view, the map used
// to just go blank with no signal. A small chip says so; re-checked on
// idle (cheap: queryRenderedFeatures respects the layer filter).

let _filterEmptyChip: HTMLElement | null = null;

function _setFilterEmptyChip(on: boolean): void {
  if (!on) {
    _filterEmptyChip?.remove();
    _filterEmptyChip = null;
    return;
  }
  if (!_filterEmptyChip) {
    _filterEmptyChip = document.createElement("div");
    _filterEmptyChip.className = "bl-filter-empty-chip";
    _filterEmptyChip.textContent = "No streams match these filters in view";
    document.body.appendChild(_filterEmptyChip);
  }
}

function _checkFilterEmpty(): void {
  if (!streamFilterExpr() || !_streamsVisible || !map.getLayer("clickable-streams")) {
    _setFilterEmptyChip(false);
    return;
  }
  const feats = map.queryRenderedFeatures(undefined, {
    layers: ["clickable-streams"],
  });
  _setFilterEmptyChip(feats.length === 0);
}

onMapReady(() => {
  map.on("idle", _checkFilterEmpty);
});

type StreamFilterPatch = {
  wild?: boolean;
  native?: boolean;
  tiers?: Partial<Record<StreamTier, boolean>>;
};

export function setStreamFilters(next: StreamFilterPatch): void {
  if (next.tiers) _filters.tiers = { ..._filters.tiers, ...next.tiers };
  if (next.wild !== undefined) _filters.wild = next.wild;
  if (next.native !== undefined) _filters.native = next.native;
  try {
    localStorage.setItem("bl_filter_wild", _filters.wild ? "1" : "0");
    localStorage.setItem("bl_filter_native", _filters.native ? "1" : "0");
    localStorage.setItem("bl_filter_tiers", ALL_TIERS.filter((t) => _filters.tiers[t]).join(","));
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

/** Verdict-color match on the `verdict` feature-state (the Phase 2
 *  mechanism, shared by the selection highlight and the condition
 *  overlay). `fallback` paints when no verdict state is set. */
function verdictMatch(fallback: unknown): unknown[] {
  return [
    "match",
    ["coalesce", ["feature-state", "verdict"], ""],
    "green", VERDICT_COLOR.green,
    "yellow", VERDICT_COLOR.yellow,
    "red", VERDICT_COLOR.red,
    "gray", VERDICT_COLOR.gray,
    fallback,
  ];
}

function colorExpr(): ExpressionSpecification {
  // Selected: verdict-colored when the river is gauged (the `verdict`
  // feature-state carries conditions.overall), flat red otherwise. The
  // "selected" signal itself stays width-8 + opacity (WIDTH_EXPR).
  // Condition-overlay matches (`cond` feature-state) paint in the same
  // verdict palette; the branch is inert while no overlay is active
  // because no feature carries the state then.
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    verdictMatch(SELECTED_FALLBACK),
    ["boolean", ["feature-state", "cond"], false],
    verdictMatch(TIER_COLOR_MATCH),
    TIER_COLOR_MATCH,
  ] as unknown as ExpressionSpecification;
}

function opacityExpr(): ExpressionSpecification {
  // While a filter overlay is active, everything that isn't the
  // selection or an overlay match fades to COND_FADE_OPACITY so the
  // matching reaches read at a glance. Swapped via setPaintProperty on
  // overlay toggle (paint-only; no refetch).
  const rest: unknown = filterOverlayActive()
    ? [
        "case",
        ["boolean", ["feature-state", "cond"], false],
        0.9,
        COND_FADE_OPACITY,
      ]
    : TIER_OPACITY_MATCH;
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    0.95,
    rest,
  ] as unknown as ExpressionSpecification;
}

// M3.2: tier is the hero — better water draws a visibly heavier line on
// top of the base streamorder width, so gold/class1 reads at a glance.
const TIER_WIDTH_BONUS: unknown = [
  "match",
  ["get", "tier"],
  "gold", 1.6,
  "class1", 0.9,
  "class2", 0.4,
  0,
];

const WIDTH_EXPR: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  8,
  [
    "+",
    ["interpolate", ["linear"], ["coalesce", ["get", "streamorder"], 3], 1, 4, 7, 7],
    TIER_WIDTH_BONUS,
  ],
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
  } as LayerSpecification, lineOverlayAnchor());
  // Transparent fat casing for touch targets; clicks bind here.
  map.addLayer({
    id: "clickable-streams-hit",
    type: "line",
    source: "clickable-streams",
    ...SRC_LAYER,
    layout: { visibility: visStr(_streamsVisible), "line-cap": "round" },
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 },
  } as LayerSpecification, lineOverlayAnchor());
  // M3.2: along-stream name labels. Symbol layer -> above the anchor
  // (appended), self-hosted glyphs (map-setup EMPTY_STYLE). Water labels
  // are conventionally italic; collision handles density.
  map.addLayer({
    id: "stream-labels",
    type: "symbol",
    source: "clickable-streams",
    ...SRC_LAYER,
    minzoom: 10,
    filter: labelFilterExpr() as never,
    layout: {
      visibility: visStr(_streamsVisible),
      "symbol-placement": "line",
      "text-field": ["get", "gnis_name"],
      "text-font": ["Noto Sans Italic"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 10, 10, 14, 13],
      "symbol-spacing": 350,
    },
    paint: {
      "text-color": "#33566b",
      "text-halo-color": "rgba(255,255,255,0.9)",
      "text-halo-width": 1.4,
    },
  } as LayerSpecification);
  // Apply the persisted wild/native filter to the freshly-added layers.
  applyStreamFilter();
  // Re-apply the selection highlight (and, while a condition filter is
  // active, the conditions overlay) as new tiles arrive (pan/zoom) --
  // fresh tiles carry no feature-state. M3.3: the reapply is O(all
  // loaded reaches) (querySourceFeatures + per-feature setFeatureState),
  // and sourcedata fires repeatedly DURING a pan gesture — running it
  // per tile batch was the main pan-time stutter. Coalesce to one pass
  // on the next idle instead.
  let _reapplyArmed = false;
  const _scheduleFeatureStateReapply = (): void => {
    if (_reapplyArmed) return;
    _reapplyArmed = true;
    map.once("idle", () => {
      _reapplyArmed = false;
      reapplyStreamHighlight();
      _applyCondOverlay();
    });
  };
  map.on("sourcedata", (e) => {
    if (e.sourceId === "clickable-streams" && e.isSourceLoaded) {
      _scheduleFeatureStateReapply();
    }
  });
  map.on("click", "clickable-streams-hit", (e) => {
    if (mapClicksClaimed()) return; // a placement/framing mode owns clicks
    const f = e.features && e.features[0];
    if (!f) return;
    onStreamClick((f.properties || {}) as ClickableStreamProps, e.lngLat);
  });
  map.on("mouseenter", "clickable-streams-hit", () => {
    if (mapClicksClaimed()) return; // keep the mode cursor
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "clickable-streams-hit", () => {
    if (mapClicksClaimed()) return;
    map.getCanvas().style.cursor = "";
  });
});

export function setStreamsVisible(on: boolean): void {
  _streamsVisible = on;
  for (const id of ["clickable-streams", "clickable-streams-hit", "stream-labels"]) {
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
  lpids: Set<number>;
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
  // Identity is the NHDPlus level path: reaches sharing a levelpathid are the
  // same river's main stem. Match on lpid so distinct creeks that merely share
  // a GNIS name -- e.g. the several unrelated "Rattling Run"s in PA -- don't all
  // light up at once. Name is only a fallback for reaches with no levelpathid.
  if (key.lpids.size) {
    return p.levelpathid != null && key.lpids.has(p.levelpathid);
  }
  return key.name != null && _normName(p.gnis_name) === key.name;
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
  lpids?: (number | null | undefined)[],
): void {
  clearStreamHighlight();
  _selVerdict = verdict;
  const name = _normName(p && p.gnis_name);
  // A selected river passes its full levelpathids set (it may span more than
  // one path across confluences / multiple gauges); a plain reach click falls
  // back to just the clicked reach's levelpathid.
  const ids = (lpids && lpids.length ? lpids : [p && p.levelpathid]).filter(
    (v): v is number => typeof v === "number",
  );
  _selStreamKey = { name: name || null, lpids: new Set(ids) };
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
  // removeFeatureState above is source-wide, so it also dropped the
  // condition overlay's `cond`/`verdict` states -- restore them.
  _applyCondOverlay();
}

// -- Filter overlay (the Filters pane: Condition + stocked + hatch) ----
// When any Filters-pane control is active (Condition dropdown not "Any",
// "Near stocked water" checked, or an Active-hatch selection), the reaches
// of every gauged river in the active catalog passing the registered
// filter predicate (riverPasses, rivers.ts) are painted in their own
// verdict color (the same `verdict` feature-state the selection highlight
// uses, plus a `cond` flag), and everything else fades via opacityExpr().
// Nothing is persisted; the filters reset on reload.

let _condFilter: ConditionKey | null = null; // Condition dropdown value
let _extraFilters = false; // stocked-only / hatch controls active
// Matching rivers indexed once per catalog refresh (value = the river's
// verdict color): O(1) lookups per feature, so each tile-batch pass is
// O(features), not O(rivers x features).
let _condIndex: {
  lpids: Map<number, ConditionKey>;
  names: Map<string, ConditionKey>;
} | null = null;

// The full Filters-pane predicate (riverPasses). Registered by rivers.ts
// at module init -- an import here would be a cycle (rivers.ts imports
// refreshConditionOverlay), same pattern as registerGaugeRenderer.
let _filterPred: ((r: River) => boolean) | null = null;

export function registerRiverFilterPredicate(fn: (r: River) => boolean): void {
  _filterPred = fn;
}

export function activeConditionFilter(): ConditionKey | null {
  return _condFilter;
}

/** True while any Filters-pane control is active (the overlay is
 *  fading non-matching reaches). Consumed by the search pool. */
export function filterOverlayActive(): boolean {
  return !!(_condFilter || _extraFilters);
}

/** Rebuild the matching-river index from the active catalog
 *  (window.allRivers -- rivers.ts keeps it pointed at whichever of
 *  viewport/state mode is current). */
function _buildCondIndex(): void {
  if (!filterOverlayActive()) {
    _condIndex = null;
    return;
  }
  const lpids = new Map<number, ConditionKey>();
  const names = new Map<string, ConditionKey>();
  for (const r of window.allRivers || []) {
    // Fall back to the Condition-only match if the predicate hasn't
    // registered yet (it lands at rivers.ts module init, before boot).
    const hit = _filterPred
      ? _filterPred(r)
      : !!r.conditions && r.conditions.overall === _condFilter;
    if (!hit) continue;
    const verdict = (r.conditions?.overall || "gray") as ConditionKey;
    for (const id of r.levelpathids || []) lpids.set(id, verdict);
    const n = _normName(r.name);
    if (n) names.set(n, verdict);
  }
  _condIndex = { lpids, names };
}

/** Set `cond` + `verdict` feature-state on every loaded reach matching
 *  the index -- levelpathid primary, normalized GNIS name fallback. Each
 *  match paints in its own river's verdict color. No-op while no filter
 *  is active. */
function _applyCondOverlay(): void {
  if (!_condIndex) return;
  const src = "clickable-streams";
  if (!map.getSource(src)) return;
  const feats = map.querySourceFeatures(src, { sourceLayer: STREAM_SOURCE_LAYER });
  for (const f of feats) {
    if (f.id == null) continue;
    const p = (f.properties || {}) as ClickableStreamProps;
    const verdict =
      (p.levelpathid != null ? _condIndex.lpids.get(p.levelpathid) : undefined) ??
      _condIndex.names.get(_normName(p.gnis_name));
    if (verdict) {
      map.setFeatureState(
        { source: src, sourceLayer: STREAM_SOURCE_LAYER, id: f.id },
        { cond: true, verdict },
      );
    }
  }
}

/** Drop all feature-state, then restore whatever should survive (the
 *  selection highlight always; the overlay only when active). */
function _resetFeatureState(): void {
  if (map.getSource("clickable-streams")) {
    map.removeFeatureState({
      source: "clickable-streams",
      sourceLayer: STREAM_SOURCE_LAYER,
    });
  }
  reapplyStreamHighlight();
  _applyCondOverlay();
}

/** Activate / switch / clear the filter overlay. Called from the
 *  Filters-pane wiring in controls.ts on any control change;
 *  `extrasActive` = the stocked-only / hatch controls are non-default.
 *  Always re-indexes (a hatch-to-hatch switch changes matches without
 *  changing either flag). */
export function setConditionOverlay(
  cond: ConditionKey | null,
  extrasActive = false,
): void {
  const wasActive = filterOverlayActive();
  _condFilter = cond;
  _extraFilters = extrasActive;
  _buildCondIndex();
  // Swap the fade in/out (paint-only). Color needs no swap: the `cond`
  // branch of colorExpr is permanent but inert without feature-state.
  if (map.getLayer("clickable-streams") && wasActive !== filterOverlayActive()) {
    map.setPaintProperty("clickable-streams", "line-opacity", opacityExpr());
  }
  _resetFeatureState();
}

/** Re-index + re-apply after the active catalog changes (state load,
 *  viewport load, the z9 mode swap -- renderRivers() calls this). Only
 *  does work while a filter is active. */
export function refreshConditionOverlay(): void {
  if (!filterOverlayActive()) return;
  _buildCondIndex();
  _resetFeatureState();
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
    panPointClearOfPanel(lngLat); // don't leave the clicked reach under the panel
    return;
  }
  // Ungauged reach: highlight it, but it's a stream selection, not a
  // river selection (selection.ts stays null). The panel is the SAME
  // full layout as a gauged river -- server-rendered popup_html with the
  // four tabs, opened on Hatches with a "no live gauge" Conditions note
  // -- fetched from /api/reach_detail so gauged and ungauged never drift.
  highlightStream(p);
  const got = prepareRiverPanel();
  if (!got) return;
  const { panel, body } = got;
  const name = _cleanName(p.gnis_name);
  // Open instantly with a skeleton (the panel snap-animates in now, not
  // after the round-trip); the server-rendered body swaps in below.
  body.innerHTML =
    `<div class="bl-card"><div class="bl-card-head">` +
    `<div class="panel-title-row"><div class="bl-title">${esc(name || "Unnamed stream")}</div></div>` +
    `<div class="bl-reach-msg">Loading&hellip;</div>` +
    `</div></div>`;
  commitRiverPanelOpen(panel, body, "auto");
  panPointClearOfPanel(lngLat); // don't leave the clicked reach under the panel
  loadReachPanel(body, lngLat, name, streamTier(p) !== "unclassified",
    p.levelpathid ?? null, p.comid ?? null);
}

// -- Ungauged-panel load ---------------------------------------------
// Fetches the server-rendered popup_html for the clicked reach and swaps
// it into the already-open panel, then wires the catch CTA + icons (the
// same post-inject steps openRiverPanel runs for a gauged river). A
// sequence guard drops a stale response if the user clicks another reach
// before this one returns.

let _reachSeq = 0;

async function loadReachPanel(
  body: HTMLElement,
  lngLat: maplibregl.LngLat | null,
  name: string | null,
  trout: boolean,
  levelpathid: number | null,
  comid: number | null,
): Promise<void> {
  const seq = ++_reachSeq;
  if (!lngLat) {
    body.innerHTML = `<div class="bl-card"><div class="bl-card-head">` +
      `<div class="bl-reach-msg">Location unavailable.</div></div></div>`;
    return;
  }
  const q = new URLSearchParams({
    lat: String(lngLat.lat),
    lon: String(lngLat.lng),
    trout: trout ? "1" : "0",
  });
  if (name) q.set("name", name);
  // W3: the reach's levelpathid lets the server answer for the whole
  // river (strongest trout designation on any flowline of its levelpath
  // group), so the panel's trout pill describes the river, not the
  // clicked pixel.
  if (levelpathid != null) q.set("levelpathid", String(levelpathid));
  let data: { popup_html?: string } | null = null;
  try {
    data = (await fetch(`/api/reach_detail?${q.toString()}`).then((r) => r.json())) as {
      popup_html?: string;
    };
  } catch (_) {
    data = null;
  }
  if (seq !== _reachSeq) return; // superseded by a newer reach click
  if (!data || !data.popup_html) {
    body.innerHTML = `<div class="bl-card"><div class="bl-card-head">` +
      `<div class="bl-reach-msg">Couldn't load reach details.</div></div></div>`;
    return;
  }
  body.innerHTML = data.popup_html;
  // Same post-inject wiring as openRiverPanel: catch CTA + lucide icons.
  // No trend / flow chart -- an ungauged reach has no gauge site.
  if (window.wireCatch) {
    window.wireCatch(body, {
      name,
      site_no: null,
      lat: lngLat.lat,
      lon: lngLat.lng,
    });
  }
  // Gradient tab: anchor on the clicked reach's comid (most precise),
  // with levelpathid + name as the fallback key.
  autoLoadElevation(body, { comid, levelpathid, name });
  refreshIcons();
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
