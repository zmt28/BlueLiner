/**
 * Rivers state machine on MapLibre GL JS (PR B2). Owns the per-state
 * catalog, viewport-vs-state mode, the filter predicate, the marker +
 * river-line renderer, and the flowline fetchers.
 *
 * MapLibre specifics vs the old Leaflet version:
 *   - Condition markers are HTML markers (maplibregl.Marker({element}))
 *     reusing the `.marker.marker--*` CSS. Each closes over its River for
 *     the click handler (replaces the `_blRiver` layer property).
 *   - River flowlines are ONE GeoJSON source ("river-lines") + a line
 *     layer. Per-river geometry is cached as Feature[] keyed by site_no;
 *     renderRivers() rebuilds the source's FeatureCollection from the
 *     rivers that currently pass the filter and have a loaded line.
 *   - Selection highlight is feature-state ("selected") with promoteId
 *     site_no, driven from river-panel.ts.
 *   - riverBySite (map-setup) is populated here so river-line clicks
 *     resolve site_no -> River.
 */

import maplibregl, { Marker } from "maplibre-gl";
import { map, onMapReady, getGeoJSON, riverBySite } from "./map-setup";
import { openRiverPanel } from "./river-panel";
import { _riverHasClickableReach } from "./streams";
import { getCurrentSt } from "./state";
import { esc } from "./util";
import { riverLngLat, boundsContainLngLat } from "./coords";
import { createMarkerTooltip } from "./popups";

const EMPTY_FC: GeoJsonFeatureCollection = { type: "FeatureCollection", features: [] };

// -- State catalog ----------------------------------------------------

let allRivers: River[] = [];
let stateRivers: River[] = [];
let viewportMode = false;

// Per-site flowline geometry (Feature[] with {site_no,color} props) +
// dedup sets. Replaces the old Map<site_no, L.GeoJSON>.
const riverLineFeatsBySite = new Map<string, GeoJsonFeature[]>();
const riverGeomLoaded = new Set<string>();
const riverGeomInFlight = new Set<string>();

const _viewportCache = new Map<string, River[]>();
let _viewportSeq = 0;

const VIEWPORT_MIN_ZOOM = 9;
const RIVER_LINE_MIN_ZOOM = 9;
const RIVER_LINE_MAX_PER_PASS = 30;
const RIVER_LINE_CONCURRENCY = 8;
const RIVER_LINE_MAX_TOTAL = 400;
let riverLinePass = 0;

let _linesToken = 0;
let _lazyRetry: ReturnType<typeof setTimeout> | null = null;

// Condition HTML markers, rebuilt each renderRivers().
let conditionMarkers: Marker[] = [];
let _markerTip: ReturnType<typeof createMarkerTooltip> | null = null;

// -- river-lines source + layer --------------------------------------

onMapReady(() => {
  map.addSource("river-lines", {
    type: "geojson",
    data: EMPTY_FC,
    promoteId: "site_no",
  });
  map.addLayer({
    id: "river-lines",
    type: "line",
    source: "river-lines",
    layout: { "line-cap": "round" },
    paint: {
      // Selected flowline turns red (matches the clickable-streams highlight);
      // otherwise the river's condition color. Without the selected case the
      // green flowline, drawn above clickable-streams, masks its red highlight.
      "line-color": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        "#e74c3c",
        ["coalesce", ["get", "color"], "#2c6fbf"],
      ],
      "line-width": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        8,
        5,
      ],
      "line-opacity": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        0.95,
        0.6,
      ],
    },
  });
  map.on("click", "river-lines", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    const sn = f.properties && (f.properties.site_no as string | undefined);
    const r = sn ? riverBySite.get(String(sn)) : null;
    if (r) openRiverPanel(r, sn ? { source: "river-lines", id: sn } : null);
  });
  map.on("mouseenter", "river-lines", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "river-lines", () => {
    map.getCanvas().style.cursor = "";
  });
  // Re-render once the source exists (initial loads may have completed
  // before map `load`).
  renderRivers();
});

// -- Filter predicate -------------------------------------------------

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

// -- Condition marker (HTML) -----------------------------------------

const CONDITION_VARIANT: Record<ConditionKey, ConditionVariant> = {
  green: "good",
  yellow: "fair",
  red: "poor",
  gray: "none",
};

function makeConditionElement(overall: ConditionKey): HTMLElement {
  const variant = CONDITION_VARIANT[overall] || "none";
  const wrap = document.createElement("div");
  wrap.className = "condition-marker-wrap";
  wrap.innerHTML = `<div class="marker marker--${variant}"></div>`;
  return wrap;
}

function tooltipHtml(r: River): string {
  const tBadge = r.on_trout
    ? ' <span style="color:var(--bl-trout);font-size:11px">Trout</span>'
    : "";
  const sBadge = r.near_stocked
    ? ' <span style="color:var(--bl-stocked);font-size:11px">Stocked</span>'
    : "";
  return (
    `<b>${esc(r.name)}</b>${tBadge}${sBadge}` +
    `<br><span style="color:${r.color}">${esc(r.label)}</span>`
  );
}

// -- Render -----------------------------------------------------------

function buildRiverLinesFC(): GeoJsonFeatureCollection {
  const features: GeoJsonFeature[] = [];
  for (const r of allRivers) {
    if (!r.site_no) continue;
    const feats = riverLineFeatsBySite.get(r.site_no);
    if (!feats) continue;
    if (!riverPasses(r)) continue;
    for (const f of feats) features.push(f);
  }
  return { type: "FeatureCollection", features };
}

/** Exactly one clickable representation per river: a flowline if loaded
 *  (drawn via the river-lines source), else a condition marker — unless
 *  the clickable-stream network already draws the river. */
export function renderRivers(): void {
  if (!_markerTip) _markerTip = createMarkerTooltip(map);
  for (const m of conditionMarkers) m.remove();
  conditionMarkers = [];
  for (const r of allRivers) {
    if (r.site_no) riverBySite.set(r.site_no, r);
    const hasLine = r.site_no ? riverLineFeatsBySite.has(r.site_no) : false;
    if (hasLine) continue; // represented by its flowline in the source
    if (!riverPasses(r)) continue;
    if (_riverHasClickableReach(r)) continue;
    const overall = (r.conditions?.overall || "gray") as ConditionKey;
    const el = makeConditionElement(overall);
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openRiverPanel(r, null);
    });
    const m = new maplibregl.Marker({ element: el, anchor: "center" })
      .setLngLat(riverLngLat(r))
      .addTo(map);
    _markerTip.bind(el, riverLngLat(r), tooltipHtml(r));
    conditionMarkers.push(m);
  }
  getGeoJSON("river-lines")?.setData(buildRiverLinesFC());
}

// -- Per-site river-line fetcher (NLDI, batched) ----------------------

async function fetchRiverLine(r: River): Promise<void> {
  if (!r.site_no) return;
  try {
    const fc = await fetch(
      `/api/river_geom?site_no=${encodeURIComponent(r.site_no)}`,
    ).then((res) => res.json());
    if (fc && fc.features && fc.features.length) {
      const feats: GeoJsonFeature[] = fc.features.map((f: GeoJsonFeature) => ({
        type: "Feature",
        properties: { site_no: r.site_no, color: r.color },
        geometry: f.geometry,
      }));
      riverLineFeatsBySite.set(r.site_no, feats);
    }
    riverGeomLoaded.add(r.site_no);
  } catch (_) {
    /* transient; later pass retries */
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
    const todo: River[] = [];
    for (const r of allRivers) {
      if (!r.site_no) continue;
      if (riverGeomLoaded.has(r.site_no) || riverGeomInFlight.has(r.site_no)) continue;
      if (!riverPasses(r)) continue;
      if (!boundsContainLngLat(map, riverLngLat(r))) continue;
      todo.push(r);
    }
    if (!todo.length) break;
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
    if (pass === riverLinePass) renderRivers();
  }
}

// -- Bulk precomputed river-line fetcher -----------------------------

async function loadRiverLines(qs: string): Promise<void> {
  let fc: GeoJsonFeatureCollection<RiverLineProps> | undefined;
  try {
    fc = await fetch(`/api/river_lines?${qs}`).then((r) => r.json());
  } catch (_) {
    return;
  }
  if (!fc || !fc.features || !fc.features.length) return;
  const riverBySiteLocal = new Map<string, River>();
  for (const r of allRivers) if (r.site_no) riverBySiteLocal.set(r.site_no, r);
  const grouped = new Map<string, GeoJsonFeature[]>();
  for (const f of fc.features) {
    const p = f.properties || ({} as RiverLineProps);
    if (!p.site_no) continue;
    if (riverLineFeatsBySite.has(p.site_no)) continue;
    const r = riverBySiteLocal.get(p.site_no);
    const color = (r && r.color) || p.color || "#2c6fbf";
    let g = grouped.get(p.site_no);
    if (!g) {
      g = [];
      grouped.set(p.site_no, g);
    }
    g.push({
      type: "Feature",
      properties: { site_no: p.site_no, color },
      geometry: f.geometry,
    });
  }
  for (const [sn, feats] of grouped) {
    riverLineFeatsBySite.set(sn, feats);
    riverGeomLoaded.add(sn);
  }
  renderRivers();
}

async function startRiverLines(qs: string): Promise<void> {
  const token = ++_linesToken;
  const delays = [0, 6000, 10000, 16000, 24000, 35000, 50000];
  for (let i = 0; i < delays.length; i++) {
    if (token !== _linesToken) return;
    if (delays[i]) {
      await new Promise((r) => setTimeout(r, delays[i]));
      if (token !== _linesToken) return;
    }
    await loadRiverLines(qs);
    if (token !== _linesToken) return;
    const missing = allRivers.some(
      (r) => r.site_no && !riverLineFeatsBySite.has(r.site_no),
    );
    if (!missing) return;
  }
}

function scheduleLazyRetry(state: string): void {
  if (_lazyRetry) clearTimeout(_lazyRetry);
  _lazyRetry = setTimeout(() => {
    if (getCurrentSt() === state && !viewportMode) loadRivers(state);
  }, 20000);
}

// -- Hybrid loading --------------------------------------------------

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
  const q = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  startRiverLines(`bbox=${encodeURIComponent(q)}`);
  loadVisibleRiverLines();
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

export async function loadRivers(state: string): Promise<void> {
  const data = await fetch(`/api/rivers?state=${state}`).then((r) => r.json());
  stateRivers = ((data && data.rivers) || []) as River[];
  window.stateRivers = stateRivers;
  riverLineFeatsBySite.clear();
  riverGeomLoaded.clear();
  riverGeomInFlight.clear();
  _viewportCache.clear();
  getGeoJSON("river-lines")?.setData(EMPTY_FC);
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();
  } else {
    viewportMode = false;
    allRivers = stateRivers;
    window.allRivers = allRivers;
    populateHatchOptions();
    renderRivers();
    if (stateRivers.length) {
      startRiverLines(`state=${encodeURIComponent(state)}`);
    } else {
      scheduleLazyRetry(state);
    }
  }
}

// -- Window bridge ----------------------------------------------------

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

window.allRivers = allRivers;
window.stateRivers = stateRivers;
window.renderRivers = renderRivers;
window.loadRivers = loadRivers;
window.loadVisibleRiverLines = loadVisibleRiverLines;
window.populateHatchOptions = populateHatchOptions;
