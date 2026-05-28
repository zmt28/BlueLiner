/**
 * River-detail panel: open/close, snap-sheet wiring, the click-
 * selection highlight state machine, and a helper for the ungauged-
 * card flow (still in app.js) to share the panel-opening primitives
 * without duplicating timer + scroll + animation logic. Extracted in
 * PR B1f.
 *
 * Owns:
 *   - the singleton panel state (_panelHideTimer, _selectedRiver,
 *     _lastPanelOpenTs) -- module-private; helpers below mediate
 *     reads/writes from outside this module
 *   - openRiverPanel(river, layer, baseStyle)  for gauged rivers
 *   - closeRiverPanel()
 *   - highlightRiver / clearRiverHighlight    for the clicked map
 *     layer's selection style
 *   - autoLoadFlowChart(root)                 primary-gauge sparkline
 *   - wireRiverPanel()                        close button + ESC +
 *                                              click-outside handlers
 *   - prepareRiverPanel + commitRiverPanelOpen helpers used by the
 *     ungauged-stream card flow in app.js (still local there for
 *     B1f; B1g's streams.ts extraction migrates that caller via
 *     direct ES import)
 *   - the river panel's snap-sheet wiring call at module init
 *
 * Window-bridged for the still-monolithic app.js (rivers / streams /
 * lines code calls openRiverPanel and the ungauged card path calls
 * prepareRiverPanel / commitRiverPanelOpen).
 *
 * Cross-module dependencies:
 *   - wireSnapSheet from ./snap-sheet
 *   - sparkline, wireSparkHover from ./sparkline (for autoLoadFlowChart)
 *   - refreshIcons from ./util
 *   - clearStreamHighlight from app.js (still there; called from
 *     closeRiverPanel via window). Future PR B1g moves it.
 *   - wireTrend, wireCatch from app.js (still there; called from
 *     openRiverPanel via window). Future PRs extract them.
 */

import { refreshIcons } from "./util";
import { sparkline, wireSparkHover } from "./sparkline";
import { wireSnapSheet } from "./snap-sheet";

// -- Panel state (module-private) --------------------------------------

let _panelHideTimer: ReturnType<typeof setTimeout> | null = null;
let _selectedRiver: { layer: L.Layer; base: L.PathOptions | null } | null = null;
let _lastPanelOpenTs = 0;

// -- Panel-open primitives ---------------------------------------------
// Shared between openRiverPanel (gauged river -> river.popup_html) and
// the ungauged-stream card flow in app.js (custom html). Both need the
// hide-timer reset + scroll-to-top + open-class transition + opened-at
// timestamp; extracting these here keeps the two callers in lockstep
// without one drifting out from under the other.

/**
 * Resolves the panel + body elements and clears any pending close
 * timer. Returns null if the panel DOM is missing (defensive).
 */
export function prepareRiverPanel(): {
  panel: HTMLElement;
  body: HTMLElement;
} | null {
  const panel = document.getElementById("river-panel");
  const body = document.getElementById("river-panel-body");
  if (!panel || !body) return null;
  if (_panelHideTimer) clearTimeout(_panelHideTimer);
  return { panel, body };
}

/**
 * Commits the panel-open transition after content has been injected.
 * `snapMode`:
 *   - "auto":  mobile = peek, desktop = full   (gauged river default)
 *   - "open":  no peek/full snap class; relies on the CSS fallback
 *              rule .open:not(.peek):not(.full)  (legacy ungauged
 *              card behaviour)
 */
export function commitRiverPanelOpen(
  panel: HTMLElement,
  body: HTMLElement,
  snapMode: "auto" | "open" = "auto",
): void {
  body.scrollTop = 0;
  panel.hidden = false;
  panel.classList.remove("peek", "full");
  requestAnimationFrame(() => {
    panel.classList.add("open");
    if (snapMode === "auto") {
      const isMobile = window.matchMedia("(max-width: 700px)").matches;
      panel.classList.add(isMobile ? "peek" : "full");
    }
  });
  _lastPanelOpenTs = Date.now();
}

// -- Open / close ------------------------------------------------------

export function openRiverPanel(
  river: River,
  layer: L.Layer | null,
  baseStyle: L.PathOptions | null,
): void {
  const got = prepareRiverPanel();
  if (!got) return;
  const { panel, body } = got;
  body.innerHTML = river.popup_html || "";
  commitRiverPanelOpen(panel, body, "auto");
  // wireTrend + wireCatch still live in app.js for now; reach via
  // window so their later extraction doesn't need to touch this file.
  if (window.wireTrend) window.wireTrend(body);
  if (window.wireCatch) window.wireCatch(body, river);
  autoLoadFlowChart(body);
  highlightRiver(layer, baseStyle);
  refreshIcons();
}

export function closeRiverPanel(): void {
  const panel = document.getElementById("river-panel");
  if (!panel || panel.hidden) return;
  panel.classList.remove("open", "peek", "full");
  _panelHideTimer = setTimeout(() => {
    panel.hidden = true;
  }, 240);
  clearRiverHighlight();
  // clearStreamHighlight lives in the clickable-streams code in
  // app.js for PR B1f; reach via window. B1g's streams.ts extraction
  // will let this be a direct ES import.
  if (window.clearStreamHighlight) window.clearStreamHighlight();
}

// -- Selection highlight (map-layer side) ------------------------------

export function highlightRiver(
  layer: L.Layer | null,
  baseStyle: L.PathOptions | null,
): void {
  clearRiverHighlight();
  if (!layer || typeof (layer as L.Path).setStyle !== "function") return;
  _selectedRiver = { layer, base: baseStyle || null };
  (layer as L.Path).setStyle({ weight: 8, opacity: 0.95 });
}

export function clearRiverHighlight(): void {
  if (
    _selectedRiver &&
    typeof (_selectedRiver.layer as L.Path).setStyle === "function" &&
    _selectedRiver.base
  ) {
    (_selectedRiver.layer as L.Path).setStyle(_selectedRiver.base);
  }
  _selectedRiver = null;
}

// -- Primary-gauge flow chart (auto-loaded on panel open) --------------

export async function autoLoadFlowChart(root: HTMLElement): Promise<void> {
  const box = root.querySelector<HTMLElement>(".bl-flow-chart[data-site]");
  if (!box || box.dataset.loaded) return;
  box.dataset.loaded = "1";
  const site = box.getAttribute("data-site") || "";
  box.innerHTML = '<div class="bl-trend-msg">Loading flow chart&hellip;</div>';
  try {
    const d = await fetch(
      `/api/history?site_no=${encodeURIComponent(site)}`,
    ).then((r) => r.json());
    box.innerHTML = sparkline(d.series);
    wireSparkHover(box);
  } catch (_) {
    box.innerHTML = '<div class="bl-trend-msg">Flow chart unavailable.</div>';
  }
}

// -- Panel chrome wiring (close button, ESC, click-outside) ------------

import { map } from "./map-setup";

export function wireRiverPanel(): void {
  const panel = document.getElementById("river-panel");
  if (!panel) return;
  panel.querySelectorAll("[data-close]").forEach((el) =>
    el.addEventListener("click", closeRiverPanel),
  );
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeRiverPanel();
  });
  // Clicking the empty map closes the panel. Guarded so the same
  // click that opened it (via a layer) doesn't immediately close it.
  map.on("click", () => {
    if (Date.now() - _lastPanelOpenTs > 300) closeRiverPanel();
  });
}

// -- Snap-sheet wiring at module init ----------------------------------
// Runs once when river-panel.ts is imported. The Vite-deferred entry
// script means DOMContentLoaded has fired, so #river-panel exists.

wireSnapSheet(document.getElementById("river-panel"), {
  cardSelector: ".river-panel-card",
  gripSelector: ".river-panel-grip",
  bodySelector: "#river-panel-body",
  tabSelector: ".bl-tab",
  onClose: closeRiverPanel,
});

// -- Window bridge for legacy app.js -----------------------------------

declare global {
  interface Window {
    openRiverPanel: typeof openRiverPanel;
    closeRiverPanel: typeof closeRiverPanel;
    prepareRiverPanel: typeof prepareRiverPanel;
    commitRiverPanelOpen: typeof commitRiverPanelOpen;
    highlightRiver: typeof highlightRiver;
    clearRiverHighlight: typeof clearRiverHighlight;
    autoLoadFlowChart: typeof autoLoadFlowChart;
    wireRiverPanel: typeof wireRiverPanel;
    // clearStreamHighlight is declared canonically in streams.ts (PR
    // B1g) as a required Window property; consumed by closeRiverPanel.
    // Wired by app.js (sparkline / catches) and consumed by openRiverPanel.
    wireTrend?: (root: HTMLElement) => void;
    wireCatch?: (root: HTMLElement, river: { name?: string }) => void;
  }
}

window.openRiverPanel = openRiverPanel;
window.closeRiverPanel = closeRiverPanel;
window.prepareRiverPanel = prepareRiverPanel;
window.commitRiverPanelOpen = commitRiverPanelOpen;
window.highlightRiver = highlightRiver;
window.clearRiverHighlight = clearRiverHighlight;
window.autoLoadFlowChart = autoLoadFlowChart;
window.wireRiverPanel = wireRiverPanel;
