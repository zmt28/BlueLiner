/**
 * Rivers state machine: the per-state catalog, the viewport-mode vs
 * state-mode switching, the filter predicate, the marker renderer,
 * and the river-line (NLDI flowline) fetcher. Extracted from
 * app.js in PR B1h.
 *
 * Owns:
 *   - allRivers / stateRivers / viewportMode (the catalog state)
 *   - riverLineBySite + riverGeomLoaded + riverGeomInFlight
 *     (the per-site flowline cache + dedup sets)
 *   - VIEWPORT_MIN_ZOOM / RIVER_LINE_MIN_ZOOM / RIVER_LINE_*
 *     constants
 *   - CONDITION_VARIANT + makeConditionIcon (the shape-coded
 *     gauge-condition divIcon factory)
 *   - populateHatchOptions (DOM: hatch-select dropdown population
 *     from allRivers's active_hatches)
 *   - riverPasses (filter predicate -- reads cond-select /
 *     trout-only / stocked-only / hatch-select)
 *   - renderRivers (the marker + line painter that drives riversLayer
 *     and decides dot-vs-line per river using
 *     _riverHasClickableReach + riverLineBySite)
 *   - loadVisibleRiverLines + fetchRiverLine (the per-site batched
 *     NLDI fetch -- center-out, concurrency-capped, request-pass
 *     race guard via riverLinePass)
 *   - loadRiverLines (the precomputed-state fetch from
 *     /api/river_lines; replaces pins with one shot for an entire
 *     state when available)
 *   - startRiverLines (the re-poll-with-backoff loop that handles
 *     a cold state's partial first response; token-cancelled when
 *     the view changes)
 *   - loadViewportRivers + refreshForView + loadRivers + the
 *     moveend listener that ties the whole zoom-in/zoom-out
 *     state-vs-viewport mode toggle together
 *   - scheduleLazyRetry (re-fetch after 20s if the state was
 *     lazy/empty on first request)
 *
 * This is the most coupled module in the migration: it touches the
 * map (zoom + bounds + moveend), every existing layer group, the
 * filter DOM, the river panel (click -> openRiverPanel), and
 * streams.ts (_riverHasClickableReach). The cohesion is real,
 * though -- all the data flows through allRivers + the line cache.
 *
 * Cross-module imports:
 *   - L from "leaflet"  (markers + geojson)
 *   - map from "./map-setup"
 *   - riversLayer, riverLinesLayer from "./map-layers"
 *   - openRiverPanel from "./river-panel"
 *   - _riverHasClickableReach from "./streams"
 *   - esc from "./util"
 *
 * Window-bridged for the still-monolithic app.js:
 *   - allRivers + stateRivers + renderRivers (already added in B1g
 *     as outgoing bridges from app.js; in B1h they reverse direction
 *     -- rivers.ts owns the writes, app.js no longer needs the
 *     bridges)
 *   - loadRivers (called from the state-selector handler + init)
 *   - renderRivers (called from filter handlers + layer toggles)
 *   - loadVisibleRiverLines (called from the filter onchange to
 *     fetch lines for newly-passing in-view rivers)
 *   - populateHatchOptions (called from init after first /api/rivers)
 *
 * NB: scheduleLazyRetry reads window.currentSt because the active
 * state code is still owned by app.js (the state-selector handler).
 * Future B1i (controls.ts) extracts that selector + currentSt moves
 * into state.ts proper; the window indirection drops then.
 */

import * as L from "leaflet";
import { map } from "./map-setup";
import { riversLayer, riverLinesLayer } from "./map-layers";
import { openRiverPanel } from "./river-panel";
import { _riverHasClickableReach } from "./streams";
import { esc } from "./util";

// -- State catalog ----------------------------------------------------
// Mutable arrays + the viewport-mode flag. These are reassigned (not
// just mutated) when the user switches states or pans across the
// viewport/state-mode boundary, so consumers in other modules must
// read via the getter pattern -- a captured const at module top
// would freeze on the initial empty array.

let allRivers: River[] = [];
let stateRivers: River[] = [];
let viewportMode = false;

// Per-site flowline layers + dedup sets.
const riverLineBySite = new Map<string, L.GeoJSON>();
const riverGeomLoaded = new Set<string>(); // site_nos with a final result (or empty)
const riverGeomInFlight = new Set<string>(); // site_nos being fetched right now

// Viewport-cache + sequence guard for loadViewportRivers's pan races.
const _viewportCache = new Map<string, River[]>(); // rounded "w,s,e,n" -> rivers
let _viewportSeq = 0;

// Zoom + batch tuning.
const VIEWPORT_MIN_ZOOM = 9;
const RIVER_LINE_MIN_ZOOM = 9;
const RIVER_LINE_MAX_PER_PASS = 30; // batch size; we loop until done
const RIVER_LINE_CONCURRENCY = 8;
const RIVER_LINE_MAX_TOTAL = 400; // safety ceiling per invocation
let riverLinePass = 0;

// Tokens for the two backoff/retry loops -- bumped on each new
// trigger so a superseded loop can no-op.
let _linesToken = 0;
let _lazyRetry: ReturnType<typeof setTimeout> | null = null;

// -- Filter predicate -------------------------------------------------

/** True when river `r` passes every active filter control. Reads
 *  the form values directly from the DOM (no cached state -- handlers
 *  call renderRivers() after each onchange so the read is always
 *  fresh at paint time). */
export function riverPasses(r: River): boolean {
  const cond = (document.getElementById("cond-select") as HTMLSelectElement).value;
  const troutOnly = (document.getElementById("trout-only") as HTMLInputElement).checked;
  const stockedOnly = (document.getElementById("stocked-only") as HTMLInputElement).checked;
  const hatch = (document.getElementById("hatch-select") as HTMLSelectElement).value;
  if (troutOnly && !r.on_trout) return false;
  if (stockedOnly && !r.near_stocked) return false;
  if (cond !== "any" && r.conditions.overall !== cond) return false;
  const ah = r.active_hatches || [];
  if (hatch === "active" && !ah.length) return false;
  if (hatch !== "any" && hatch !== "active" && !ah.includes(hatch)) return false;
  return true;
}

// -- Hatch dropdown population ---------------------------------------

/** Populates #hatch-select with the union of `active_hatches` across
 *  allRivers. Preserves the current selection if it's still present;
 *  falls back to "any". Called after each catalog load. */
export function populateHatchOptions(): void {
  const sel = document.getElementById("hatch-select") as HTMLSelectElement;
  if (!sel) return;
  const cur = sel.value;
  const set = new Set<string>();
  allRivers.forEach((r) =>
    (r.active_hatches || []).forEach((h) => set.add(h)),
  );
  const insects = [...set].sort();
  sel.innerHTML =
    '<option value="any">Any</option>' +
    '<option value="active">Active now</option>' +
    insects.map((i) => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "any";
}

// -- Condition marker --------------------------------------------------

const CONDITION_VARIANT: Record<ConditionKey, ConditionVariant> = {
  green: "good",
  yellow: "fair",
  red: "poor",
  gray: "none",
};

/** Shape-coded condition marker. Color + shape so colorblind anglers
 *  get the same signal: filled disc for Good, filled + center dot
 *  for Fair, filled + horizontal bar for Poor, dashed outline for
 *  No data. CSS styling for the four variants lives in app.css
 *  under .marker*. */
export function makeConditionIcon(overall: ConditionKey): L.DivIcon {
  const variant = CONDITION_VARIANT[overall] || "none";
  return L.divIcon({
    className: "condition-marker-wrap",
    html: `<div class="marker marker--${variant}"></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

// -- Render -----------------------------------------------------------

/** Exactly one clickable representation per river:
 *
 *    1. The NLDI flowline if loaded (riverLinesLayer), else
 *    2. Skip if the clickable-stream network already draws this
 *       river somewhere in the viewport -- the user can click the
 *       line, so a gauge dot here would just be a redundant target
 *       landing on the gauge centroid (which can fall on an
 *       unrelated tributary), else
 *    3. A pin (fallback for low zoom / clickable layer off / no
 *       matching NHD reach, e.g. tiny named creeks the network
 *       filters out).
 */
export function renderRivers(): void {
  riversLayer.clearLayers();
  for (const r of allRivers) {
    const line = r.site_no ? riverLineBySite.get(r.site_no) : null;
    const pass = riverPasses(r);
    if (line) {
      if (pass && !riverLinesLayer.hasLayer(line))
        riverLinesLayer.addLayer(line);
      if (!pass && riverLinesLayer.hasLayer(line))
        riverLinesLayer.removeLayer(line);
      continue; // line represents this river -- no redundant pin
    }
    if (!pass) continue;
    if (_riverHasClickableReach(r)) continue; // clickable network has it
    const overall = (r.conditions?.overall || "gray") as ConditionKey;
    const m = L.marker([r.lat, r.lon], {
      icon: makeConditionIcon(overall),
    });
    const tBadge = r.on_trout
      ? ' <span style="color:var(--bl-trout);font-size:11px">Trout</span>'
      : "";
    const sBadge = r.near_stocked
      ? ' <span style="color:var(--bl-stocked);font-size:11px">Stocked</span>'
      : "";
    m.bindTooltip(
      `<b>${esc(r.name)}</b>${tBadge}${sBadge}` +
        `<br><span style="color:${r.color}">${esc(r.label)}</span>`,
    );
    m._blRiver = r;
    m.on("click", () => openRiverPanel(r, m, null));
    riversLayer.addLayer(m);
  }
}

// -- Per-site river-line fetcher (NLDI, batched) ----------------------

async function fetchRiverLine(r: River): Promise<void> {
  if (!r.site_no) return;
  try {
    const fc = await fetch(
      `/api/river_geom?site_no=${encodeURIComponent(r.site_no)}`,
    ).then((res) => res.json());
    // Empty geometry is a final answer (NLDI has no flowline) ->
    // pin fallback; mark loaded so we don't refetch it.
    if (fc && fc.features && fc.features.length) {
      const line = L.geoJSON(fc, {
        style: { color: r.color, weight: 5, opacity: 0.6 },
      });
      line._blRiver = r;
      line.on("click", () =>
        openRiverPanel(r, line, {
          color: r.color,
          weight: 5,
          opacity: 0.6,
        }),
      );
      riverLineBySite.set(r.site_no, line); // renderRivers() places it
    }
    riverGeomLoaded.add(r.site_no);
  } catch (_) {
    // Transient failure: leave it unloaded so a later pass retries.
  } finally {
    riverGeomInFlight.delete(r.site_no);
  }
}

export async function loadVisibleRiverLines(): Promise<void> {
  if (map.getZoom() < RIVER_LINE_MIN_ZOOM) return;
  const pass = ++riverLinePass;
  const c = map.getCenter();
  let fetched = 0;

  while (fetched < RIVER_LINE_MAX_TOTAL && pass === riverLinePass) {
    if (map.getZoom() < RIVER_LINE_MIN_ZOOM) return;
    const b = map.getBounds();
    const todo: River[] = [];
    for (const r of allRivers) {
      if (!r.site_no) continue;
      if (
        riverGeomLoaded.has(r.site_no) ||
        riverGeomInFlight.has(r.site_no)
      )
        continue;
      if (!riverPasses(r)) continue; // don't fetch filtered-out rivers
      if (!b.contains([r.lat, r.lon])) continue;
      todo.push(r);
    }
    if (!todo.length) break;
    // Center-out: the rivers the user is looking at fill in first.
    todo.sort(
      (a, z) =>
        (a.lat - c.lat) ** 2 +
        (a.lon - c.lng) ** 2 -
        ((z.lat - c.lat) ** 2 + (z.lon - c.lng) ** 2),
    );
    const batch = todo.slice(0, RIVER_LINE_MAX_PER_PASS);
    let i = 0;
    const worker = async () => {
      while (i < batch.length && pass === riverLinePass) {
        // Mark in-flight only for the one we're about to fetch, so
        // a superseded pass can't strand markers (fetchRiverLine
        // clears them in its finally).
        const r = batch[i++];
        if (r.site_no) riverGeomInFlight.add(r.site_no);
        await fetchRiverLine(r);
      }
    };
    await Promise.all(
      Array.from(
        { length: Math.min(RIVER_LINE_CONCURRENCY, batch.length) },
        worker,
      ),
    );
    fetched += batch.length;
    if (pass === riverLinePass) renderRivers(); // lines progressively replace pins
  }
}

// -- Bulk precomputed river-line fetcher (one Postgres read) ---------

/** Draw EVERY river as its precomputed flowline in one shot, at any
 *  zoom. /api/river_lines is a single gzipped Postgres read (no
 *  per-river NLDI fan-out), so lines appear immediately instead of
 *  trickling in. */
async function loadRiverLines(qs: string): Promise<void> {
  let fc: GeoJsonFeatureCollection<RiverLineProps> | undefined;
  try {
    fc = await fetch(`/api/river_lines?${qs}`).then((r) => r.json());
  } catch (_) {
    return; // keep pins; transient failure
  }
  if (!fc || !fc.features || !fc.features.length) return;
  const bySite = new Map<
    string,
    { type: "FeatureCollection"; features: typeof fc.features; color?: string }
  >();
  for (const f of fc.features) {
    const p = f.properties || ({} as RiverLineProps);
    if (!p.site_no) continue;
    let g = bySite.get(p.site_no);
    if (!g) {
      g = { type: "FeatureCollection", features: [], color: p.color };
      bySite.set(p.site_no, g);
    }
    g.features.push(f);
  }
  const riverBySite = new Map<string, River>();
  for (const r of allRivers) if (r.site_no) riverBySite.set(r.site_no, r);
  for (const [sn, g] of bySite) {
    if (riverLineBySite.has(sn)) continue;
    const r = riverBySite.get(sn);
    const color = (r && r.color) || g.color || "#2c6fbf";
    const line = L.geoJSON(g, {
      style: { color, weight: 5, opacity: 0.6 },
    });
    if (r) {
      line._blRiver = r;
      line.on("click", () =>
        openRiverPanel(r, line, { color, weight: 5, opacity: 0.6 }),
      );
    }
    riverLineBySite.set(sn, line);
    riverGeomLoaded.add(sn); // per-site fallback now skips it
  }
  renderRivers(); // lines replace pins
}

/** Geometry is backfilled into Postgres asynchronously, so on a cold
 *  state the first /api/river_lines may be partial/empty. Re-poll
 *  with backoff, merging newly-ready lines, until every river has
 *  one (or we give up -- some gauges genuinely have no NLDI
 *  flowline and stay pins). The token cancels the loop the moment
 *  the state/viewport changes. */
async function startRiverLines(qs: string): Promise<void> {
  const token = ++_linesToken;
  const delays = [0, 6000, 10000, 16000, 24000, 35000, 50000];
  for (let i = 0; i < delays.length; i++) {
    if (token !== _linesToken) return; // superseded by a newer view
    if (delays[i]) {
      await new Promise((r) => setTimeout(r, delays[i]));
      if (token !== _linesToken) return;
    }
    await loadRiverLines(qs);
    if (token !== _linesToken) return;
    const missing = allRivers.some(
      (r) => r.site_no && !riverLineBySite.has(r.site_no),
    );
    if (!missing) return; // fully covered -> done
  }
}

/** A lazy (never-visited) state returns [] while the background
 *  precompute runs; refetch once so it fills in without the user
 *  reloading. Reads window.currentSt at retry time (the active code
 *  may have changed in the 20s window) -- currentSt is still owned
 *  by app.js until B1i extracts the state selector. */
function scheduleLazyRetry(state: string): void {
  if (_lazyRetry) clearTimeout(_lazyRetry);
  _lazyRetry = setTimeout(() => {
    if (window.currentSt === state && !viewportMode) loadRivers(state);
  }, 20000);
}

// -- Hybrid loading: state overview when zoomed out, live viewport
// when zoomed in --

async function loadViewportRivers(): Promise<void> {
  const b = map.getBounds();
  const round = (x: number) => x.toFixed(2);
  const key = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(round)
    .join(",");
  const seq = ++_viewportSeq;
  let rivers = _viewportCache.get(key);
  if (!rivers) {
    try {
      const q = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
      const data = await fetch(
        `/api/rivers?bbox=${encodeURIComponent(q)}`,
      ).then((r) => r.json());
      rivers = ((data && data.rivers) || []) as River[];
      _viewportCache.set(key, rivers);
      if (_viewportCache.size > 30) {
        const first = _viewportCache.keys().next().value;
        if (first !== undefined) _viewportCache.delete(first);
      }
    } catch (_) {
      return; // keep current view; transient failure
    }
  }
  if (seq !== _viewportSeq) return; // a newer pan/zoom superseded us
  viewportMode = true;
  allRivers = rivers;
  window.allRivers = allRivers; // bridge: streams.ts reads via window
  populateHatchOptions();
  renderRivers();
  const q = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  startRiverLines(`bbox=${encodeURIComponent(q)}`); // batched + re-poll
  loadVisibleRiverLines(); // per-site fallback (zoomed-in only)
}

export function refreshForView(): void {
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();
  } else if (viewportMode) {
    viewportMode = false; // zoomed back out -> state overview
    allRivers = stateRivers;
    window.allRivers = allRivers; // bridge: streams.ts reads via window
    populateHatchOptions();
    renderRivers();
  }
}

let _viewTimer: ReturnType<typeof setTimeout> | null = null;
map.on("moveend", () => {
  if (_viewTimer) clearTimeout(_viewTimer);
  _viewTimer = setTimeout(refreshForView, 400);
});

export async function loadRivers(state: string): Promise<void> {
  const data = await fetch(`/api/rivers?state=${state}`).then((r) => r.json());
  stateRivers = ((data && data.rivers) || []) as River[];
  window.stateRivers = stateRivers; // bridge: streams.ts reads via window
  riverLinesLayer.clearLayers();
  riverLineBySite.clear();
  riverGeomLoaded.clear();
  riverGeomInFlight.clear();
  _viewportCache.clear();
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers(); // already zoomed in: viewport drives
  } else {
    viewportMode = false;
    allRivers = stateRivers;
    window.allRivers = allRivers; // bridge: streams.ts reads via window
    populateHatchOptions();
    renderRivers();
    if (stateRivers.length) {
      startRiverLines(`state=${encodeURIComponent(state)}`);
    } else {
      scheduleLazyRetry(state); // not computed yet -> auto-fill
    }
  }
}

// -- Window bridge for legacy app.js ----------------------------------
// `allRivers`, `stateRivers`, and `renderRivers` were bridged
// outgoing from app.js in PR B1g (streams.ts read via window). Now
// that rivers.ts owns the writes, they reverse direction: rivers.ts
// writes window so streams.ts continues to read them transparently.
// PR B1g's app.js code that mirrors on each reassignment is removed
// in this PR (app.js no longer owns the writes).

declare global {
  interface Window {
    allRivers: River[];
    stateRivers: River[];
    renderRivers: typeof renderRivers;
    loadRivers: typeof loadRivers;
    loadVisibleRiverLines: typeof loadVisibleRiverLines;
    populateHatchOptions: typeof populateHatchOptions;
  }
}

// Initial assignments (and ongoing mirror so streams.ts sees
// reassignments). We mutate the same array references when
// possible, but loadRivers/loadViewportRivers/refreshForView do
// reassign -- so we update window on the same lines.

window.allRivers = allRivers;
window.stateRivers = stateRivers;
window.renderRivers = renderRivers;
window.loadRivers = loadRivers;
window.loadVisibleRiverLines = loadVisibleRiverLines;
window.populateHatchOptions = populateHatchOptions;

// The three reassignment sites above (loadViewportRivers,
// refreshForView, loadRivers) inline-update window.allRivers /
// window.stateRivers after each rebind. The bridges drop in a
// later PR once the only consumer (streams.ts's _gaugedRiverFor)
// imports them directly via ES module.
