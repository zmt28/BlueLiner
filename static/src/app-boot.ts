// Deferred app bootstrap (PR B2f). Everything map-coupled — MapLibre GL
// JS plus every map module — lives in this chunk, which main.ts loads via
// a dynamic import after the static chrome shell + CSS have painted. So
// the ~300 KB gzipped renderer never blocks first paint.
//
// Each module wires its own DOM + window bridges as an import side
// effect (same order dependency as before); init() runs the async
// bootstrap.

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
import "./legend";
import "./search";
import { initAuth } from "./auth";
import "./catches";
import { loadFavorites } from "./favorites";
import { loadPins } from "./pins";

import { refreshIcons } from "./util";

async function init(): Promise<void> {
  const list: ApiState[] = await fetch("/api/states").then((r) => r.json());
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
  setCurrentSt(state, { syncUrl: false });
  sel.value = state;
  document.dispatchEvent(new Event("bl:states-loaded"));
  await mapReady();
  map.jumpTo({ center: centerLngLat(getStates()[state].center), zoom: STATE_ZOOM });
  wireRiverPanel();
  loadRivers(state);
  loadPins();
  await initAuth();
  void loadFavorites(); // needs the signed-in state initAuth resolved
}

init();

// Hydrate the static <i data-lucide> nodes in the chrome once Lucide's
// deferred CDN script has parsed.
if (document.readyState === "complete") refreshIcons();
else window.addEventListener("load", () => refreshIcons());
