/**
 * Rivers state machine on MapLibre GL JS. Owns the per-state catalog, the
 * viewport-vs-state mode, the filter predicate, and the per-gauge condition
 * icon renderer.
 *
 * Condition discs (`.marker.marker--*`, one HTML marker per USGS gauge at
 * the gauge's own lat/lon) render only for the SELECTED river: selection.ts
 * calls showGaugesFor / hideSelectedGauges (registered at module init), so
 * the map carries no catalog-wide markers. Stream geometry + tier color come
 * from the clickable-streams vector tiles (streams.ts); tapping a reach (or
 * a search result) is what selects a river.
 */

import maplibregl, { Marker } from "maplibre-gl";
import { map } from "./map-setup";
import {
  selectRiver,
  registerGaugeRenderer,
  refreshSelectedRiver,
} from "./selection";
import { getCurrentSt, getStates } from "./state";
import { esc } from "./util";
import { createMarkerTooltip } from "./popups";
import {
  refreshConditionOverlay,
  registerRiverFilterPredicate,
} from "./streams";

// -- State catalog ----------------------------------------------------

let allRivers: River[] = [];
let stateRivers: River[] = [];
let viewportMode = false;

const _viewportCache = new Map<string, River[]>();
let _viewportSeq = 0;
const VIEWPORT_MIN_ZOOM = 9;
let _lazyRetry: ReturnType<typeof setTimeout> | null = null;

// Per-gauge condition HTML markers for the selected river only,
// rebuilt by showGaugesFor() / removed by hideSelectedGauges().
let gaugeMarkers: Marker[] = [];
let _markerTip: ReturnType<typeof createMarkerTooltip> | null = null;

// -- Filter predicate -------------------------------------------------

export function riverPasses(r: River): boolean {
  const cond = (document.getElementById("cond-select") as HTMLSelectElement).value;
  const stockedOnly = (document.getElementById("stocked-only") as HTMLInputElement).checked;
  const hatch = (document.getElementById("hatch-select") as HTMLSelectElement).value;
  if (stockedOnly && !r.near_stocked) return false;
  if (cond !== "any" && r.conditions.overall !== cond) return false;
  const ah = r.active_hatches || [];
  if (hatch === "active" && !ah.length) return false;
  if (hatch !== "any" && hatch !== "active" && !ah.includes(hatch)) return false;
  return true;
}

// -- Hatch dropdown population ---------------------------------------

export function populateHatchOptions(): void {
  const sel = document.getElementById("hatch-select") as HTMLSelectElement;
  if (!sel) return;
  const cur = sel.value;
  const set = new Set<string>();
  allRivers.forEach((r) => (r.active_hatches || []).forEach((h) => set.add(h)));
  const insects = [...set].sort();
  sel.innerHTML =
    '<option value="any">Any</option>' +
    '<option value="active">Active now</option>' +
    insects.map((i) => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "any";
}

// -- Gauge condition icon (HTML marker) ------------------------------

const CONDITION_VARIANT: Record<ConditionKey, ConditionVariant> = {
  green: "good",
  yellow: "fair",
  red: "poor",
  gray: "none",
};

const CONDITION_LABEL: Record<ConditionKey, string> = {
  green: "Good",
  yellow: "Fair",
  red: "Poor",
  gray: "No data",
};

function makeConditionElement(overall: ConditionKey): HTMLElement {
  const variant = CONDITION_VARIANT[overall] || "none";
  const wrap = document.createElement("div");
  wrap.className = "condition-marker-wrap";
  wrap.innerHTML = `<div class="marker marker--${variant}"></div>`;
  return wrap;
}

function gaugeTooltip(g: GaugePoint, r: River): string {
  const overall = (g.conditions?.overall || "gray") as ConditionKey;
  const label = CONDITION_LABEL[overall] || "No data";
  return `<b>${esc(g.site_name || r.name)}</b><br>${esc(label)}`;
}

// -- Selected-river gauge discs ---------------------------------------
// Condition discs render only for the selected river: selectRiver()
// shows them, clearRiverSelection() removes them, refreshSelectedRiver()
// re-renders them when fresh catalog data lands (so verdicts stay
// current). Registered with selection.ts at module init.

export function showGaugesFor(river: River): void {
  if (!_markerTip) _markerTip = createMarkerTooltip(map);
  hideSelectedGauges();
  for (const g of river.gauges || []) {
    if (g.lat == null || g.lon == null) continue;
    const overall = (g.conditions?.overall || "gray") as ConditionKey;
    const el = makeConditionElement(overall);
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      // Its river is already selected; re-selecting just re-opens the
      // panel (harmless when it's already open).
      selectRiver(river);
    });
    const ll: [number, number] = [g.lon, g.lat];
    const m = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat(ll)
      .addTo(map);
    _markerTip.bind(el, ll, gaugeTooltip(g, river));
    gaugeMarkers.push(m);
  }
}

export function hideSelectedGauges(): void {
  for (const m of gaugeMarkers) m.remove();
  gaugeMarkers = [];
}

// -- Render hook -------------------------------------------------------
// The catalog-wide per-gauge marker loop lived here until the gauges-
// on-selection change. This remains the hook the loaders + filter
// controls call after the catalog changes: re-resolve the selection
// against the new River objects so its discs pick up fresh verdicts.

export function renderRivers(): void {
  refreshSelectedRiver(allRivers);
  // The conditions overlay (Condition filter) indexes the active catalog;
  // re-match it against the fresh River objects. No-op while inactive.
  refreshConditionOverlay();
}

// -- Hybrid loading: state overview when zoomed out, live viewport when in --

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
      return;
    }
  }
  if (seq !== _viewportSeq) return;
  viewportMode = true;
  allRivers = rivers;
  window.allRivers = allRivers;
  populateHatchOptions();
  renderRivers();
}

export function refreshForView(): void {
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();
  } else if (viewportMode) {
    viewportMode = false;
    allRivers = stateRivers;
    window.allRivers = allRivers;
    populateHatchOptions();
    renderRivers();
  }
}

let _viewTimer: ReturnType<typeof setTimeout> | null = null;
map.on("moveend", () => {
  if (_viewTimer) clearTimeout(_viewTimer);
  _viewTimer = setTimeout(refreshForView, 400);
});

function scheduleLazyRetry(state: string): void {
  if (_lazyRetry) clearTimeout(_lazyRetry);
  _lazyRetry = setTimeout(() => {
    if (getCurrentSt() === state && !viewportMode) loadRivers(state);
  }, 20000);
}

// -- State-load feedback chip -----------------------------------------
// Switching states used to be silent: the map jumped, then rivers popped
// in whenever the fetch returned — and a not-yet-precomputed state showed
// an empty map with a hidden 20s retry. The chip narrates both.

let _stateChip: HTMLElement | null = null;

function setStateChip(text: string | null): void {
  if (text === null) {
    _stateChip?.remove();
    _stateChip = null;
    return;
  }
  if (!_stateChip) {
    _stateChip = document.createElement("div");
    _stateChip.className = "bl-state-chip";
    document.body.appendChild(_stateChip);
  }
  _stateChip.textContent = text;
}

function stateName(code: string): string {
  return getStates()[code]?.name || code;
}

export async function loadRivers(state: string): Promise<void> {
  const name = stateName(state);
  setStateChip(`Loading ${name}…`);
  let data: { rivers?: River[] } | null = null;
  try {
    data = (await fetch(`/api/rivers?state=${state}`).then((r) => r.json())) as {
      rivers?: River[];
    };
  } catch {
    data = null; // network failure -> same retry path as "not computed yet"
  }
  if (getCurrentSt() !== state) return; // user switched again mid-flight
  stateRivers = ((data && data.rivers) || []) as River[];
  window.stateRivers = stateRivers;
  _viewportCache.clear();
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    setStateChip(null); // viewport mode narrates via its own fetch below
    loadViewportRivers();
  } else {
    viewportMode = false;
    allRivers = stateRivers;
    window.allRivers = allRivers;
    populateHatchOptions();
    renderRivers();
    if (!stateRivers.length) {
      // Lazy state: the server assembles it on first visit. Say so
      // instead of showing a silently empty map.
      setStateChip(`Preparing ${name} — first visit can take a minute…`);
      scheduleLazyRetry(state);
    } else {
      setStateChip(null);
    }
  }
}

// -- Selection registration --------------------------------------------
// Hand the disc renderer to selection.ts (registration instead of an
// import there: this module imports selectRiver, so the reverse import
// would be a cycle).

registerGaugeRenderer({ show: showGaugesFor, hide: hideSelectedGauges });

// Hand the Filters-pane predicate to the overlay index builder in
// streams.ts (registration, not an import there: streams.ts is imported
// by this module, so the reverse import would be a cycle).
registerRiverFilterPredicate(riverPasses);

// -- Window bridge ----------------------------------------------------

declare global {
  interface Window {
    allRivers: River[];
    stateRivers: River[];
    renderRivers: typeof renderRivers;
    loadRivers: typeof loadRivers;
    populateHatchOptions: typeof populateHatchOptions;
  }
}

window.allRivers = allRivers;
window.stateRivers = stateRivers;
window.renderRivers = renderRivers;
window.loadRivers = loadRivers;
window.populateHatchOptions = populateHatchOptions;
