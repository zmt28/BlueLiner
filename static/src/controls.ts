/**
 * UI wiring for everything in the page chrome that drives the map:
 *   - state selector dropdown
 *   - filter controls (cond / hatch / trout-only / stocked-only) +
 *     the reset button
 *   - color-mode segmented control (trout class vs conditions)
 *   - base-map segmented control (street / satellite / topo)
 *   - layer-visibility checkboxes (6) + bl_layers localStorage
 *     persistence + wireLayerToggle helper
 *   - the unified controls panel (Layers / Filters / Legend tabbed
 *     sheet, with 3 header-button direct-entry points, ESC + click-
 *     outside close, in-panel tab switching, and the snap-sheet
 *     wiring for the mobile bottom-sheet behavior)
 *
 * This module is the boundary between "the map" (rivers / streams /
 * layers / panels) and "the page UI that toggles them." Extracted in
 * PR B1i; replaces the ~250 lines of inline wiring at the bottom of
 * the legacy app.js.
 *
 * Cross-module deps:
 *   - state: getStates, setCurrentSt, STATE_ZOOM
 *   - map-setup: map, currentBaseKey, setBaseMap
 *   - map-layers: the 6 layer groups + ensureTrout + ensureAccess +
 *     loadPublicLands + the reset helpers
 *   - snap-sheet: wireSnapSheet
 *   - streams: setStreamColorMode, restyleStreams, clickableLayer,
 *     loadClickableStreams
 *   - rivers: loadRivers, renderRivers, loadVisibleRiverLines
 */

import { map, currentBaseKey, setBaseMap } from "./map-setup";
import {
  troutLayer,
  accessLayer,
  publicLandsLayer,
  riversLayer,
  pinsLayer,
  ensureTrout,
  ensureAccess,
  loadPublicLands,
  resetTroutLoadedState,
  resetAccessLoadedState,
} from "./map-layers";
import {
  clickableLayer,
  loadClickableStreams,
  setStreamColorMode,
  restyleStreams,
} from "./streams";
import {
  loadRivers,
  loadVisibleRiverLines,
  renderRivers,
} from "./rivers";
import { getStates, setCurrentSt, STATE_ZOOM } from "./state";
import { refreshIcons } from "./util";

// -- Filter controls -------------------------------------------------
// Each onchange re-runs the filter predicate + fetches lines for any
// newly-passing in-view rivers (so a filter that lets MORE rivers
// through doesn't leave them as pins when their flowline could be
// loaded).

function onFilterChange(): void {
  renderRivers();
  loadVisibleRiverLines();
}

(document.getElementById("cond-select") as HTMLSelectElement).onchange = onFilterChange;
(document.getElementById("trout-only") as HTMLInputElement).onchange = onFilterChange;
(document.getElementById("stocked-only") as HTMLInputElement).onchange = onFilterChange;
(document.getElementById("hatch-select") as HTMLSelectElement).onchange = onFilterChange;

// -- State selector --------------------------------------------------

(document.getElementById("state-select") as HTMLSelectElement).onchange = (e) => {
  const s = (e.target as HTMLSelectElement).value;
  setCurrentSt(s); // updates URL + window mirror
  const catalog = getStates();
  map.setView(catalog[s].center, STATE_ZOOM);
  loadRivers(s);
  // Refresh trout / access for the new state only if the layer is
  // currently shown. Routed through the setters in map-layers.ts
  // (B1e: a bare `troutLoadedState = null` was ReferenceError in
  // strict mode since the var is module-private there).
  resetTroutLoadedState();
  if (map.hasLayer(troutLayer)) ensureTrout(s);
  resetAccessLoadedState();
  if (map.hasLayer(accessLayer)) ensureAccess(s);
};

// -- Color-mode segmented control (trout class vs conditions) -------
// Restyles the clickable stream network. It's a viewing aid, not a
// filter, so it groups under "Show on map" rather than "Show
// rivers".

document.querySelectorAll<HTMLButtonElement>("#color-mode button").forEach((b) =>
  b.addEventListener("click", () => {
    const mode = b.dataset.mode as "trout" | "conditions";
    setStreamColorMode(mode);
    document.querySelectorAll("#color-mode button").forEach((x) =>
      x.classList.toggle("on", x === b),
    );
    restyleStreams();
  }),
);

// -- Map chrome: rail (desktop) / tab bar (mobile) + side panel/sheet
// The left rail tabs, the mobile bottom-tab-bar, and the account avatar
// all toggle the one shared #controls-panel. On desktop it's a continuous
// side panel anchored to the rail; on mobile it's a bottom sheet. Only one
// pane is visible at a time; re-tapping the active tab (or X / backdrop /
// ESC) closes it.

type PanelTab = "layers" | "filters" | "legend" | "content" | "profile";

const PANEL_TITLES: Record<PanelTab, string> = {
  layers: "Map Layers",
  filters: "Map Filters & Settings",
  legend: "Map Legend",
  content: "My Content",
  profile: "My Profile",
};

const MOBILE_BP = "(max-width: 759px)";
const RAIL_W = 88;
const PANEL_W = 340;

const panel = document.getElementById("controls-panel") as HTMLElement;
const panelTitle = document.getElementById("panel-title") as HTMLElement;
const panelCard = panel.querySelector(".panel-card") as HTMLElement;
const railTabs = Array.from(
  document.querySelectorAll<HTMLButtonElement>("#rail-tabs .rail-tab"),
);
const mobileTabs = Array.from(
  document.querySelectorAll<HTMLButtonElement>("#mobile-tabbar .mobile-tab"),
);
const panes: Record<string, HTMLElement> = {};
panel.querySelectorAll<HTMLElement>(".panel-pane").forEach((p) => {
  if (p.dataset.pane) panes[p.dataset.pane] = p;
});

let activeTab: PanelTab | null = null;
let panelHideTimer: ReturnType<typeof setTimeout> | null = null;

function isMobileView(): boolean {
  return window.matchMedia(MOBILE_BP).matches;
}

/** Shift the floating chrome (pills + controls) to the right of the rail,
 *  and further right when the side panel is open. On mobile the chrome
 *  spans the full width. */
function setChromeOffset(): void {
  const left = isMobileView() ? 0 : RAIL_W + (activeTab !== null ? PANEL_W : 0);
  document.documentElement.style.setProperty("--map-left", `${left}px`);
}

function reflectActive(): void {
  for (const b of [...railTabs, ...mobileTabs]) {
    const on = b.dataset.tab === activeTab;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-expanded", on ? "true" : "false");
  }
  document
    .getElementById("rail-avatar")
    ?.classList.toggle("is-active", activeTab === "profile");
}

function showPane(tab: PanelTab): void {
  for (const [id, el] of Object.entries(panes)) {
    el.classList.toggle("is-active", id === tab);
  }
  panelTitle.textContent = PANEL_TITLES[tab];
}

function openPanel(tab: PanelTab): void {
  if (panelHideTimer) {
    clearTimeout(panelHideTimer);
    panelHideTimer = null;
  }
  activeTab = tab;
  showPane(tab);
  panel.hidden = false;
  panelCard.style.transform = ""; // clear any leftover drag transform
  requestAnimationFrame(() => panel.classList.add("open"));
  reflectActive();
  setChromeOffset();
  refreshIcons();
}

function closePanel(): void {
  if (panel.hidden) return;
  panel.classList.remove("open");
  activeTab = null;
  reflectActive();
  setChromeOffset();
  panelHideTimer = setTimeout(() => {
    panel.hidden = true;
  }, 300);
}

function toggleTab(tab: PanelTab): void {
  if (activeTab === tab) closePanel();
  else openPanel(tab);
}

for (const b of [...railTabs, ...mobileTabs]) {
  b.addEventListener("click", () => toggleTab(b.dataset.tab as PanelTab));
}
document
  .getElementById("rail-avatar")
  ?.addEventListener("click", () => toggleTab("profile"));
document
  .getElementById("mobile-avatar")
  ?.addEventListener("click", () => toggleTab("profile"));
document.getElementById("content-drop-pin")?.addEventListener("click", () => {
  closePanel();
  document.getElementById("drop-pin")?.click();
});

// X button + (mobile) backdrop -> close.
panel
  .querySelectorAll<HTMLElement>("[data-close]")
  .forEach((el) => el.addEventListener("click", closePanel));

// ESC closes from any state.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !panel.hidden) closePanel();
});

// Keep the chrome offset correct across the breakpoint.
window.addEventListener("resize", setChromeOffset);
setChromeOffset();

// Mobile: drag the sheet handle down to dismiss.
(function wireSheetDrag(): void {
  const handle = panel.querySelector<HTMLElement>("[data-grip]");
  if (!handle) return;
  let startY = 0;
  let dy = 0;
  let dragging = false;
  handle.addEventListener("pointerdown", (e) => {
    if (!isMobileView()) return;
    dragging = true;
    startY = e.clientY;
    dy = 0;
    panelCard.classList.add("dragging");
    try {
      handle.setPointerCapture(e.pointerId);
    } catch (_) {
      /* not all browsers */
    }
  });
  handle.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    dy = Math.max(0, e.clientY - startY);
    panelCard.style.transform = `translateY(${dy}px)`;
  });
  function end(): void {
    if (!dragging) return;
    dragging = false;
    panelCard.classList.remove("dragging");
    const shouldClose = dy > 90;
    panelCard.style.transform = "";
    if (shouldClose) closePanel();
  }
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
})();

// -- Bottom-right map controls (zoom / compass / locate) -----------

document.getElementById("zoom-in")?.addEventListener("click", () => map.zoomIn());
document.getElementById("zoom-out")?.addEventListener("click", () => map.zoomOut());
// Leaflet renders north-up (no rotation), so the compass is a north
// indicator; clicking it re-centers without changing zoom.
document
  .getElementById("compass-btn")
  ?.addEventListener("click", () => map.setView(map.getCenter(), map.getZoom()));

const locateBtn = document.getElementById("locate-btn") as HTMLButtonElement | null;
if (locateBtn) {
  locateBtn.addEventListener("click", () => {
    locateBtn.classList.add("is-active");
    map.locate({ setView: true, maxZoom: 13 });
  });
  map.on("locationfound", () => locateBtn.classList.remove("is-active"));
  map.on("locationerror", () => locateBtn.classList.remove("is-active"));
}

// -- State pill (mirrors the hidden native #state-select) ----------
// The native <select id="state-select"> stays the canonical value +
// keyboard target; selecting from the pill writes it and dispatches a
// change so the existing state-selector handler (above) does the work.

const statePill = document.getElementById("state-pill") as HTMLButtonElement;
const statePillName = document.getElementById("state-pill-name") as HTMLElement;
const stateMenu = document.getElementById("state-menu") as HTMLElement;
const stateSelectEl = document.getElementById("state-select") as HTMLSelectElement;

function updateStatePillName(): void {
  const opt = stateSelectEl.options[stateSelectEl.selectedIndex];
  statePillName.textContent = opt ? opt.textContent : stateSelectEl.value;
}

function buildStateMenu(query: string): void {
  const q = query.trim().toLowerCase();
  const rows: string[] = [];
  for (const opt of Array.from(stateSelectEl.options)) {
    const code = opt.value;
    const name = opt.textContent || code;
    if (q && !name.toLowerCase().includes(q) && !code.toLowerCase().includes(q))
      continue;
    const active = code === stateSelectEl.value;
    rows.push(
      `<button type="button" class="state-menu-item${active ? " is-active" : ""}" data-code="${code}">` +
        `<span class="state-pill-flag">${code}</span><span>${name}</span>` +
        `${active ? '<i data-lucide="check"></i>' : ""}</button>`,
    );
  }
  const safe = query.replace(/"/g, "&quot;");
  stateMenu.innerHTML =
    `<div class="state-menu-search"><i data-lucide="search"></i>` +
    `<input type="text" placeholder="Search states…" id="state-menu-input" value="${safe}"></div>` +
    (rows.length ? rows.join("") : `<div class="state-menu-empty">No states</div>`);
  refreshIcons();
  const input = document.getElementById("state-menu-input") as HTMLInputElement | null;
  if (input) {
    input.addEventListener("input", () => buildStateMenu(input.value));
    if (query) {
      input.focus();
      input.setSelectionRange(query.length, query.length);
    }
  }
  stateMenu.querySelectorAll<HTMLButtonElement>(".state-menu-item").forEach((b) =>
    b.addEventListener("click", () => {
      const code = b.dataset.code;
      if (code && code !== stateSelectEl.value) {
        stateSelectEl.value = code;
        stateSelectEl.dispatchEvent(new Event("change"));
      }
      updateStatePillName();
      closeStateMenu();
    }),
  );
}

function openStateMenu(): void {
  buildStateMenu("");
  stateMenu.hidden = false;
  statePill.setAttribute("aria-expanded", "true");
  setTimeout(() => document.getElementById("state-menu-input")?.focus(), 0);
}

function closeStateMenu(): void {
  stateMenu.hidden = true;
  statePill.setAttribute("aria-expanded", "false");
}

statePill.addEventListener("click", (e) => {
  e.stopPropagation();
  if (stateMenu.hidden) openStateMenu();
  else closeStateMenu();
});
document.addEventListener("click", (e) => {
  if (stateMenu.hidden) return;
  const t = e.target as Node | null;
  if (t && (stateMenu.contains(t) || statePill.contains(t))) return;
  closeStateMenu();
});
// Reflect both pill-driven and programmatic (init) selection changes.
stateSelectEl.addEventListener("change", updateStatePillName);
document.addEventListener("bl:states-loaded", updateStatePillName);
updateStatePillName();

// -- Layer visibility checkboxes + bl_layers persistence -----------
// Saved preference per layer id (e.g. "lyr-fishable" -> true). New
// layers added after a user's last visit use the HTML `checked`
// default until they explicitly toggle.

const LAYER_PREF_KEY = "bl_layers";

function loadLayerPrefs(): LayerPrefs {
  try {
    const raw = localStorage.getItem(LAYER_PREF_KEY);
    return raw ? (JSON.parse(raw) as LayerPrefs) : {};
  } catch (_) {
    return {};
  }
}

function saveLayerPref(id: string, on: boolean): void {
  try {
    const prefs = loadLayerPrefs();
    prefs[id] = !!on;
    localStorage.setItem(LAYER_PREF_KEY, JSON.stringify(prefs));
  } catch (_) {
    /* localStorage unavailable; in-memory state still reflects */
  }
}

const _layerPrefs: LayerPrefs = loadLayerPrefs();

function wireLayerToggle(
  id: string,
  layer: L.Layer,
  onAdd?: () => void,
): void {
  const cb = document.getElementById(id) as HTMLInputElement;
  // Apply saved preference if present, otherwise leave the HTML
  // default.
  if (Object.prototype.hasOwnProperty.call(_layerPrefs, id)) {
    cb.checked = !!_layerPrefs[id];
  }
  if (cb.checked && !map.hasLayer(layer)) {
    map.addLayer(layer);
    if (onAdd) onAdd();
  } else if (!cb.checked && map.hasLayer(layer)) {
    map.removeLayer(layer);
  }
  cb.addEventListener("change", () => {
    if (cb.checked) {
      map.addLayer(layer);
      if (onAdd) onAdd();
    } else {
      map.removeLayer(layer);
    }
    saveLayerPref(id, cb.checked);
  });
}

wireLayerToggle("lyr-fishable", clickableLayer, loadClickableStreams);
// Toggling the clickable layer off needs to bring the gauge dots back
// (since _riverHasClickableReach now returns false), so re-render.
(document.getElementById("lyr-fishable") as HTMLInputElement).addEventListener(
  "change",
  (e) => {
    if (!(e.target as HTMLInputElement).checked) renderRivers();
  },
);
wireLayerToggle("lyr-usgs", window.hydroLayer);
// Trout + access ensure-loaders read the CURRENT state at click time
// via getCurrentSt() (state may have changed since module-init).
wireLayerToggle("lyr-trout", troutLayer, () =>
  ensureTrout(window.getCurrentSt()),
);
wireLayerToggle("lyr-access", accessLayer, () =>
  ensureAccess(window.getCurrentSt()),
);
wireLayerToggle("lyr-public-lands", publicLandsLayer, loadPublicLands);
wireLayerToggle("lyr-pins", pinsLayer);

// Reference riversLayer so TS doesn't drop the import (kept because
// future regressions might land a toggle for it; the rivers layer
// itself is always added to the map at init in map-layers.ts).
void riversLayer;

// -- Base-map segmented control ------------------------------------

const basemapSeg = document.getElementById("basemap-mode");
if (basemapSeg) {
  // Reflect the loaded preference on the segment buttons.
  const initialKey = currentBaseKey();
  for (const btn of basemapSeg.querySelectorAll<HTMLButtonElement>(
    "button[data-base]",
  )) {
    btn.classList.toggle("on", btn.dataset.base === initialKey);
    btn.addEventListener("click", () => {
      const key = btn.dataset.base as "street" | "satellite" | "topo";
      setBaseMap(key);
      for (const sib of basemapSeg.querySelectorAll<HTMLButtonElement>(
        "button[data-base]",
      )) {
        sib.classList.toggle("on", sib.dataset.base === key);
      }
    });
  }
}

// -- Viewport watcher: refetch the clickable network + public lands
// when the user settles after panning/zooming. Debounced 500 ms so
// touch-device momentum-pans don't fire two fetches per gesture --
// iOS Safari and Android Chrome both emit a moveend at the start of
// the deceleration AND at the rest point. (rivers.ts's own moveend
// listener for refreshForView is independent; both subscribers
// coexist on the same event.) -----------------------------------------

let _streamTimer: ReturnType<typeof setTimeout> | null = null;
let _publicLandsTimer: ReturnType<typeof setTimeout> | null = null;
map.on("moveend", () => {
  if (_streamTimer) clearTimeout(_streamTimer);
  _streamTimer = setTimeout(loadClickableStreams, 500);
  if (_publicLandsTimer) clearTimeout(_publicLandsTimer);
  _publicLandsTimer = setTimeout(loadPublicLands, 500);
});

// -- Reset filters button -----------------------------------------

(document.getElementById("filter-reset") as HTMLButtonElement).onclick = () => {
  (document.getElementById("cond-select") as HTMLSelectElement).value = "any";
  (document.getElementById("hatch-select") as HTMLSelectElement).value = "any";
  (document.getElementById("trout-only") as HTMLInputElement).checked = false;
  (document.getElementById("stocked-only") as HTMLInputElement).checked = false;
  onFilterChange();
};
