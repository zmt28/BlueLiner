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
import { wireSnapSheet } from "./snap-sheet";

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

// -- Controls panel: Layers / Filters / Legend ---------------------
// Three direct-entry header buttons open the panel to their tab. Each
// button: opens panel if closed, switches tab if open on a different
// tab, closes if open on its own tab.

const controlsPanel = document.getElementById("controls-panel") as HTMLElement;
type CtrlTab = "layers" | "filters" | "legend";

const _ctrlTabRadios: Record<CtrlTab, HTMLInputElement> = {
  layers: document.getElementById("ctrl-tab-layers") as HTMLInputElement,
  filters: document.getElementById("ctrl-tab-filters") as HTMLInputElement,
  legend: document.getElementById("ctrl-tab-legend") as HTMLInputElement,
};
const _ctrlHeaderBtns: Record<CtrlTab, HTMLButtonElement> = {
  layers: document.getElementById("ctrl-layers") as HTMLButtonElement,
  filters: document.getElementById("ctrl-filters") as HTMLButtonElement,
  legend: document.getElementById("ctrl-legend") as HTMLButtonElement,
};
let _ctrlActiveTab: CtrlTab = "layers";
let _ctrlHideTimer: ReturnType<typeof setTimeout> | null = null;

function _setCtrlHeaderActive(tab: CtrlTab | null): void {
  for (const [t, btn] of Object.entries(_ctrlHeaderBtns)) {
    const on =
      t === tab && !controlsPanel.hidden && controlsPanel.classList.contains("open");
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-expanded", on ? "true" : "false");
  }
}

function _selectCtrlTab(tab: CtrlTab): void {
  const radio = _ctrlTabRadios[tab];
  if (radio) radio.checked = true;
  _ctrlActiveTab = tab;
  _setCtrlHeaderActive(tab);
}

function openControlsPanel(tab: CtrlTab): void {
  if (_ctrlHideTimer) clearTimeout(_ctrlHideTimer);
  _selectCtrlTab(tab);
  controlsPanel.hidden = false;
  const isMobile = window.matchMedia("(max-width: 700px)").matches;
  controlsPanel.classList.remove("peek", "full");
  requestAnimationFrame(() => {
    controlsPanel.classList.add("open");
    if (isMobile) controlsPanel.classList.add("peek");
    _setCtrlHeaderActive(tab);
  });
}

function closeControlsPanel(): void {
  if (!controlsPanel || controlsPanel.hidden) return;
  controlsPanel.classList.remove("open", "peek", "full");
  _ctrlHideTimer = setTimeout(() => {
    controlsPanel.hidden = true;
  }, 240);
  _setCtrlHeaderActive(null);
}

// Header buttons: tab direct-entry.
for (const [tab, btn] of Object.entries(_ctrlHeaderBtns) as [CtrlTab, HTMLButtonElement][]) {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !controlsPanel.hidden && controlsPanel.classList.contains("open");
    if (open && _ctrlActiveTab === tab) {
      closeControlsPanel();
    } else if (open) {
      _selectCtrlTab(tab);
      // On mobile a tab switch while at peek means the user is
      // engaging with the panel; promote to full (matching the
      // river-panel rule).
      if (
        window.matchMedia("(max-width: 700px)").matches &&
        controlsPanel.classList.contains("peek")
      ) {
        controlsPanel.classList.remove("peek");
        controlsPanel.classList.add("full");
      }
    } else {
      openControlsPanel(tab);
    }
  });
}

// X button + backdrop -> close.
controlsPanel.querySelectorAll<HTMLElement>("[data-close]").forEach((el) =>
  el.addEventListener("click", closeControlsPanel),
);

// ESC closes from any state.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !controlsPanel.hidden) closeControlsPanel();
});

// Click-outside closes (desktop popover behavior).
document.addEventListener("click", (e) => {
  if (controlsPanel.hidden) return;
  const target = e.target as Node | null;
  if (target && controlsPanel.contains(target)) return;
  if (
    target &&
    Object.values(_ctrlHeaderBtns).some((b) => b.contains(target))
  )
    return;
  closeControlsPanel();
});

// In-panel tab switching: clicking a tab label updates the header's
// active highlight.
for (const [tab, radio] of Object.entries(_ctrlTabRadios) as [CtrlTab, HTMLInputElement][]) {
  radio.addEventListener("change", () => {
    if (radio.checked) {
      _ctrlActiveTab = tab;
      _setCtrlHeaderActive(tab);
    }
  });
}

// Snap-sheet behavior on mobile.
wireSnapSheet(controlsPanel, {
  cardSelector: ".controls-panel-card",
  gripSelector: ".controls-panel-grip",
  bodySelector: "#controls-panel-body",
  tabSelector: ".ctrl-tab",
  onClose: closeControlsPanel,
});

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
