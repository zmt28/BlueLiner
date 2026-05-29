// Blueliner frontend entry point (Vite-managed).
//
// Imports CSS for Vite's asset graph, then every TS module (each one
// wires up its own DOM at module-init), then runs init() which does
// the async bootstrap: fetch /api/states, set the initial state code +
// view, kick off the first rivers + pins + auth fetches.
//
// PR B1j removes the last "legacy app.js" -- every concern is now a
// TS module under static/src/. This file is the entire orchestrator;
// there is no static/src/app.js anymore.

import "../tokens.css";
import "../app.css";
import "maplibre-gl/dist/maplibre-gl.css";

// Module-init order matters: leaf modules first, then modules that
// depend on them. Each one wires up its own DOM + window bridges as
// a side effect of being imported.
import {
  STATE_ZOOM,
  setStates,
  setCurrentSt,
  currentState,
  getStates,
} from "./state";
import "./util";
import "./sparkline";
import { map, mapReady } from "./map-setup";
import { centerLngLat } from "./coords";
import "./map-layers";
import "./snap-sheet";
import { wireRiverPanel } from "./river-panel";
import "./streams";
import { loadRivers } from "./rivers";
import "./controls";
import "./search";
// Last three: auth + catches + pins all import from the modules above
// (auth needs DEVICE_HEADER from state; catches needs sparkline + auth;
// pins needs map-layers). They wire their own DOM at module-init.
import { initAuth } from "./auth";
import "./catches";
import { loadPins } from "./pins";

import { refreshIcons } from "./util";

// -- Async bootstrap ------------------------------------------------

async function init(): Promise<void> {
  const list: ApiState[] = await fetch("/api/states").then((r) => r.json());
  // Populate the canonical state catalog (mutates state.ts's _states
  // in place; existing window.STATES references stay valid).
  setStates(list);
  const sel = document.getElementById("state-select") as HTMLSelectElement;
  sel.innerHTML = "";
  for (const s of list) {
    const opt = document.createElement("option");
    opt.value = s.code;
    opt.textContent = s.name;
    sel.appendChild(opt);
  }
  const state = currentState();
  // Init reads the active code from the URL, so syncing it back into
  // the URL is a no-op -- skip via syncUrl:false.
  setCurrentSt(state, { syncUrl: false });
  sel.value = state;
  // Let the floating state pill mirror the populated select.
  document.dispatchEvent(new Event("bl:states-loaded"));
  // Wait for the MapLibre style `load` so overlay sources/layers exist
  // before the first data loads paint into them.
  await mapReady();
  map.jumpTo({ center: centerLngLat(getStates()[state].center), zoom: STATE_ZOOM });
  wireRiverPanel();
  loadRivers(state);
  loadPins();
  await initAuth();
}

init();

// Hydrate the static <i data-lucide="..."> nodes in the page shell
// (header tab buttons, sign-in mailbox). Wrapped in load so the
// deferred CDN script has finished parsing before we call into it.
window.addEventListener("load", () => refreshIcons());

// -- Service worker -------------------------------------------------
// Auto-reload once when a new service worker takes control, so a
// deploy propagates fresh JS/CSS without a manual cache clear. Only
// armed when the page is already controlled (a returning visit) --
// on the very first visit there's no controller yet and no stale
// assets to replace, so we skip the reload to avoid a pointless
// first-load refresh.

if ("serviceWorker" in navigator) {
  if (navigator.serviceWorker.controller) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      refreshing = true;
      window.location.reload();
    });
  }
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* ignore */
    });
  });
}
