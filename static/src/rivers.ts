/**
 * Rivers state machine on MapLibre GL JS. Owns the per-state catalog, the
 * viewport-vs-state mode, the filter predicate, and the per-gauge condition
 * icon renderer.
 *
 * Conditions are shown as one HTML marker per USGS gauge -- at the gauge's
 * own lat/lon, colored by that gauge's condition (`.marker.marker--*`).
 * Stream geometry + trout-class color come from the clickable-streams vector
 * tiles (streams.ts); clicking a gauge icon opens the river panel and
 * highlights that river's reaches. (The old condition-colored NLDI flowline
 * layer + its per-gauge fetching were removed when conditions moved onto the
 * gauge icons.)
 */

import maplibregl, { Marker } from "maplibre-gl";
import { map } from "./map-setup";
import { openRiverPanel } from "./river-panel";
import { highlightStream } from "./streams";
import { getCurrentSt } from "./state";
import { esc } from "./util";
import { createMarkerTooltip } from "./popups";

// -- State catalog ----------------------------------------------------

let allRivers: River[] = [];
let stateRivers: River[] = [];
let viewportMode = false;

const _viewportCache = new Map<string, River[]>();
let _viewportSeq = 0;
const VIEWPORT_MIN_ZOOM = 9;
let _lazyRetry: ReturnType<typeof setTimeout> | null = null;

// Per-gauge condition HTML markers, rebuilt each renderRivers().
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

// -- Render: one condition icon per gauge ----------------------------

export function renderRivers(): void {
  if (!_markerTip) _markerTip = createMarkerTooltip(map);
  for (const m of gaugeMarkers) m.remove();
  gaugeMarkers = [];
  for (const r of allRivers) {
    if (!riverPasses(r)) continue;
    for (const g of r.gauges || []) {
      if (g.lat == null || g.lon == null) continue;
      const overall = (g.conditions?.overall || "gray") as ConditionKey;
      const el = makeConditionElement(overall);
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openRiverPanel(r);
        // Highlight the river's reaches in the clickable network.
        highlightStream({
          gnis_name: r.name,
          levelpathid:
            r.levelpathids && r.levelpathids.length ? r.levelpathids[0] : null,
        } as ClickableStreamProps);
      });
      const ll: [number, number] = [g.lon, g.lat];
      const m = new maplibregl.Marker({ element: el, anchor: "center" })
        .setLngLat(ll)
        .addTo(map);
      _markerTip.bind(el, ll, gaugeTooltip(g, r));
      gaugeMarkers.push(m);
    }
  }
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

export async function loadRivers(state: string): Promise<void> {
  const data = await fetch(`/api/rivers?state=${state}`).then((r) => r.json());
  stateRivers = ((data && data.rivers) || []) as River[];
  window.stateRivers = stateRivers;
  _viewportCache.clear();
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();
  } else {
    viewportMode = false;
    allRivers = stateRivers;
    window.allRivers = allRivers;
    populateHatchOptions();
    renderRivers();
    if (!stateRivers.length) scheduleLazyRetry(state); // not computed yet
  }
}

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
