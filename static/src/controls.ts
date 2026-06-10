/**
 * UI wiring for everything in the page chrome that drives the map:
 *   - state selector dropdown
 *   - filter controls (cond / hatch / stocked-only) +
 *     the reset button
 *   - base-map segmented control (street / satellite / topo)
 *   - layer-visibility checkboxes + bl_layers localStorage
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
 *   - map-layers: the layer visibility setters + ensureAccess
 *     + the reset helpers
 *   - streams: loadClickableStreams, setStreamsVisible
 *   - rivers: loadRivers, renderRivers
 */

import { map, currentBaseKey, setBaseMap, setHydroVisible } from "./map-setup";
import {
  BASEMAP_TILES_ENABLED,
  BASEMAP_TILES_URL,
  BASEMAP_STYLE_URL,
  STREAM_TILES_URL,
} from "./config";
import {
  downloadArea,
  estimateArea,
  offlineStats,
  offlineMeta,
  clearOffline,
  type Archive,
  type BBox,
} from "./offline-tiles";
import {
  ensureAccess,
  resetAccessLoadedState,
  setAccessVisible,
  setStockedVisible,
  refreshStockedForState,
  setPublicLandsVisible,
} from "./map-layers";
import {
  loadClickableStreams,
  setStreamsVisible,
  setStreamFilters,
  currentStreamFilters,
} from "./streams";
import {
  loadRivers,
  renderRivers,
} from "./rivers";
import { setPinsVisible } from "./pins";
import { getStates, setCurrentSt, STATE_ZOOM } from "./state";
import { centerLngLat } from "./coords";
import { refreshIcons } from "./util";

// -- Filter controls -------------------------------------------------
// Each onchange re-runs the filter predicate, which rebuilds the
// per-gauge condition markers for the rivers that now pass.

function onFilterChange(): void {
  renderRivers();
}

(document.getElementById("cond-select") as HTMLSelectElement).onchange = onFilterChange;
(document.getElementById("stocked-only") as HTMLInputElement).onchange = onFilterChange;
(document.getElementById("hatch-select") as HTMLSelectElement).onchange = onFilterChange;

// -- State selector --------------------------------------------------

(document.getElementById("state-select") as HTMLSelectElement).onchange = (e) => {
  const s = (e.target as HTMLSelectElement).value;
  setCurrentSt(s); // updates URL + window mirror
  const catalog = getStates();
  map.jumpTo({ center: centerLngLat(catalog[s].center), zoom: STATE_ZOOM });
  loadRivers(s);
  // Refresh access for the new state only if the layer is currently
  // shown (read the checkbox; the MapLibre layers always exist, only
  // their visibility toggles).
  resetAccessLoadedState();
  if ((document.getElementById("lyr-access") as HTMLInputElement).checked) ensureAccess(s);
  // Stocked markers reload for the new state if they're showing (toggle or
  // the Stocked map style); refreshStockedForState owns that decision.
  refreshStockedForState(s);
};

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

// Selecting any map POI (river / stream / access / pin) closes the rail
// panel so the opened drawer/popup isn't hidden behind it. POI handlers
// dispatch "bl:poi-open"; closePanel() no-ops when nothing is open.
document.addEventListener("bl:poi-open", () => closePanel());

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

// MapLibre supports rotation/pitch, so the compass resets the map to
// north-up; its needle rotates to track the current bearing.
const compassBtn = document.getElementById("compass-btn");
if (compassBtn) {
  compassBtn.addEventListener("click", () =>
    map.easeTo({ bearing: 0, pitch: 0 }),
  );
  map.on("rotate", () => {
    compassBtn.style.transform = `rotate(${-map.getBearing()}deg)`;
  });
}

// MapLibre has no map.locate; use the Geolocation API + flyTo.
const locateBtn = document.getElementById("locate-btn") as HTMLButtonElement | null;
if (locateBtn) {
  locateBtn.addEventListener("click", () => {
    if (!navigator.geolocation) return;
    locateBtn.classList.add("is-active");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        locateBtn.classList.remove("is-active");
        map.flyTo({
          center: [pos.coords.longitude, pos.coords.latitude],
          zoom: 13,
        });
      },
      () => locateBtn.classList.remove("is-active"),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  });
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

/**
 * Wire a layer-visibility checkbox to a show/hide setter. MapLibre
 * layers always exist (added on map load); we only toggle their
 * `visibility`. `onShow` runs the lazy fetch the first time (and each
 * time) the layer is shown. Applies the saved preference immediately so
 * the desired state is set before the map `load` event mounts the layers
 * (the setters store the desired flag and re-apply on mount).
 */
function wireLayerToggle(
  id: string,
  setVisible: (on: boolean) => void,
  onShow?: () => void,
): void {
  const cb = document.getElementById(id) as HTMLInputElement;
  if (Object.prototype.hasOwnProperty.call(_layerPrefs, id)) {
    cb.checked = !!_layerPrefs[id];
  }
  setVisible(cb.checked);
  if (cb.checked && onShow) onShow();
  cb.addEventListener("change", () => {
    setVisible(cb.checked);
    if (cb.checked && onShow) onShow();
    saveLayerPref(id, cb.checked);
  });
}

wireLayerToggle("lyr-fishable", setStreamsVisible, loadClickableStreams);
wireLayerToggle("lyr-usgs", setHydroVisible);
// The access ensure-loader reads the CURRENT state at show time via
// getCurrentSt() (state may have changed since module-init).
wireLayerToggle("lyr-access", setAccessVisible, () =>
  ensureAccess(window.getCurrentSt()),
);
// setStockedVisible already triggers the lazy load via its visibility apply,
// so no onShow callback is needed here.
wireLayerToggle("lyr-stocked", setStockedVisible);
wireLayerToggle("lyr-public-lands", setPublicLandsVisible);
wireLayerToggle("lyr-pins", setPinsVisible);

// -- Base-map segmented control ------------------------------------

const basemapSeg = document.getElementById("basemap-mode");
if (basemapSeg) {
  // The vector base only exists when a basemap archive is configured at build
  // time (VITE_BASEMAP_TILES_URL). Drop its tile otherwise so it isn't offered.
  if (!BASEMAP_TILES_ENABLED) {
    basemapSeg.querySelector('button[data-base="vector"]')?.remove();
  }
  // Reflect the loaded preference on the segment buttons.
  const initialKey = currentBaseKey();
  for (const btn of basemapSeg.querySelectorAll<HTMLButtonElement>(
    "button[data-base]",
  )) {
    btn.classList.toggle("on", btn.dataset.base === initialKey);
    btn.addEventListener("click", () => {
      const key = btn.dataset.base as "street" | "satellite" | "topo" | "vector";
      setBaseMap(key);
      for (const sib of basemapSeg.querySelectorAll<HTMLButtonElement>(
        "button[data-base]",
      )) {
        sib.classList.toggle("on", sib.dataset.base === key);
      }
    });
  }
}

// -- Connectivity badge --------------------------------------------
// Shows an "Offline" pill whenever the browser reports no network, so a user
// off-grid knows the map is running on downloaded data.

const netBadge = document.getElementById("net-badge");
if (netBadge) {
  const reflectNet = (): void => {
    netBadge.hidden = navigator.onLine;
  };
  window.addEventListener("online", reflectNet);
  window.addEventListener("offline", reflectNet);
  reflectNet();
}

// -- Offline maps: download the current view (Phase 2) -------------
// Cache the basemap + streams tiles (and the basemap assets) for the current
// viewport into IndexedDB / the SW cache so they render with the network cut.
// Cumulative across taps. Behind the basemap flag + IndexedDB support.

const offlineSection = document.getElementById("offline-test");
if (offlineSection && BASEMAP_TILES_ENABLED && "indexedDB" in window) {
  offlineSection.hidden = false;
  const dlBtn = document.getElementById("offline-download") as HTMLButtonElement;
  const clearBtn = document.getElementById("offline-clear") as HTMLButtonElement;
  const status = document.getElementById("offline-status") as HTMLElement;
  const mb = (b: number): string => `${(b / 1048576).toFixed(1)} MB`;

  const archives: Archive[] = [{ url: BASEMAP_TILES_URL, label: "base" }];
  if (STREAM_TILES_URL) archives.push({ url: STREAM_TILES_URL, label: "streams" });

  async function refreshStatus(): Promise<void> {
    const [s, m] = await Promise.all([offlineStats(), offlineMeta()]);
    if (m.downloads > 0 && s.bytes > 0) {
      const when = m.lastAt ? new Date(m.lastAt).toLocaleDateString() : "";
      status.textContent = `Saved offline: ${mb(s.bytes)} across ${m.downloads} area(s). Last ${when}.`;
      clearBtn.hidden = false;
    } else {
      status.textContent = "Save the current map view to use it without a signal.";
      clearBtn.hidden = true;
    }
  }
  void refreshStatus();

  dlBtn.addEventListener("click", async () => {
    const b = map.getBounds();
    const bbox: BBox = { w: b.getWest(), s: b.getSouth(), e: b.getEast(), n: b.getNorth() };
    const z = Math.floor(map.getZoom());
    const total = await estimateArea(bbox, archives, z);
    if (total > 6000) {
      status.textContent = `This area is too large (${total.toLocaleString()} tiles). Zoom in and try again.`;
      return;
    }
    dlBtn.disabled = true;
    clearBtn.hidden = true;
    status.textContent = "Preparing…";
    try {
      const r = await downloadArea(bbox, archives, z, BASEMAP_STYLE_URL, (p) => {
        status.textContent =
          p.phase === "assets"
            ? "Saving map style…"
            : `Saving tiles ${p.done.toLocaleString()}/${p.total.toLocaleString()}…`;
      });
      status.textContent =
        `Saved ${mb(r.bytes)} for offline use. ` +
        `Test it: enable airplane mode, reopen, pick Vector, and pan this area.`;
      clearBtn.hidden = false;
    } catch (e) {
      status.textContent = `Download failed: ${(e as Error).message}`;
    } finally {
      dlBtn.disabled = false;
    }
  });

  clearBtn.addEventListener("click", async () => {
    clearBtn.disabled = true;
    status.textContent = "Removing…";
    try {
      await clearOffline();
    } finally {
      clearBtn.disabled = false;
      await refreshStatus();
    }
  });
}

// -- Stream filters (wild / native) -------------------------------
// Two toggles layered over the tier coloring: reflect persisted state and on
// change re-filter the network via setFilter (no refetch).

const _initialStreamFilters = currentStreamFilters();
function wireStreamFilter(id: string, key: "wild" | "native"): void {
  const el = document.getElementById(id) as HTMLInputElement | null;
  if (!el) return;
  el.checked = _initialStreamFilters[key];
  el.addEventListener("change", () => setStreamFilters({ [key]: el.checked }));
}
wireStreamFilter("filter-wild", "wild");
wireStreamFilter("filter-native", "native");

// -- Viewport watcher: refetch the clickable network + public lands
// when the user settles after panning/zooming. Debounced 500 ms so
// touch-device momentum-pans don't fire two fetches per gesture --
// iOS Safari and Android Chrome both emit a moveend at the start of
// the deceleration AND at the rest point. (rivers.ts's own moveend
// listener for refreshForView is independent; both subscribers
// coexist on the same event.) -----------------------------------------

let _streamTimer: ReturnType<typeof setTimeout> | null = null;
map.on("moveend", () => {
  if (_streamTimer) clearTimeout(_streamTimer);
  _streamTimer = setTimeout(loadClickableStreams, 500);
});

// -- Reset filters button -----------------------------------------

(document.getElementById("filter-reset") as HTMLButtonElement).onclick = () => {
  (document.getElementById("cond-select") as HTMLSelectElement).value = "any";
  (document.getElementById("hatch-select") as HTMLSelectElement).value = "any";
  (document.getElementById("stocked-only") as HTMLInputElement).checked = false;
  onFilterChange();
};
