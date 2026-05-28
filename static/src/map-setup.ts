/**
 * Leaflet map initialization + base-map management. Extracted from the
 * legacy app.js (PR B1d).
 *
 * Owns:
 *   - the singleton Leaflet map instance (`map`)
 *   - the three base tile providers (CARTO Light / Esri Satellite /
 *     USGS Topographic)
 *   - active base-map state + bl_basemap localStorage persistence
 *   - the USGS Hydro Cached overlay (labeled rivers/streams)
 *   - the global popupopen -> refreshIcons() listener (any Leaflet
 *     popup that contains <i data-lucide="..."> nodes needs Lucide
 *     to hydrate them when the popup mounts to the DOM; doing it
 *     once at map-init is the single place that covers all layers)
 *
 * Window-bridged for the still-monolithic app.js:
 *   - map           used by every layer + event handler in app.js
 *   - setBaseMap    called from the controls panel handler
 *   - currentBaseKey  read by controls to set initial segment
 *   - hydroLayer    referenced by the layer-toggle checkbox
 *
 * Future modules consume via ES import (no window indirection).
 *
 * NB: this module's top-level code calls `L.map("map")`, which
 * mounts to the #map element in static/index.html. Vite's <script
 * type="module"> tag is deferred so DOMContentLoaded has fired
 * before this runs -- the #map div exists.
 */

import * as L from "leaflet";
import { refreshIcons } from "./util";

// Expose L to the legacy app.js. Until PR B1d, Leaflet was loaded as
// a global `<script src="/static/vendor/leaflet/leaflet.js">` and
// app.js read the resulting window.L. Now Vite bundles the npm
// `leaflet` package into the main chunk; we re-publish it on window
// so app.js's bare `L.tileLayer(...)`, `L.marker(...)`, etc. resolve
// to the SAME instance map-setup.ts uses for the singleton map.
// (Bundled vs vendor-global L would have separate prototype chains
// and break instanceof + Leaflet internals.) The vendor script tag
// in index.html is removed in this PR.
window.L = L;

type BaseMapKey = "street" | "satellite" | "topo";

// Base maps. Exactly one is on the map at a time; the active one
// sits at the bottom of the layer stack so overlays (hydro, streams,
// gauges) always render on top. Defaults to "street" (CARTO Light);
// the user's last choice persists in localStorage["bl_basemap"]
// across visits.
const BASE_MAPS: Record<BaseMapKey, () => L.TileLayer> = {
  street: () =>
    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      {
        attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
        subdomains: "abcd",
        maxZoom: 19,
      },
    ),
  satellite: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        attribution: "Source: Esri, Maxar, Earthstar Geographics",
        maxZoom: 19,
      },
    ),
  topo: () =>
    L.tileLayer(
      "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
      {
        attribution:
          "USGS The National Map: National Boundaries Dataset, " +
          "National Elevation Dataset, Geographic Names Information System.",
        maxZoom: 16,
      },
    ),
};

function loadBaseMapPref(): BaseMapKey {
  try {
    const v = localStorage.getItem("bl_basemap");
    return v && v in BASE_MAPS ? (v as BaseMapKey) : "street";
  } catch (_) {
    return "street";
  }
}

export const map: L.Map = L.map("map");

// Single global popupopen listener -- every layer that binds a popup
// inherits Lucide hydration without having to register its own.
map.on("popupopen", () => refreshIcons());

let _currentBaseKey: BaseMapKey = loadBaseMapPref();
let currentBaseLayer: L.TileLayer = BASE_MAPS[_currentBaseKey]().addTo(map);

export function setBaseMap(key: BaseMapKey): void {
  if (!BASE_MAPS[key] || key === _currentBaseKey) return;
  map.removeLayer(currentBaseLayer);
  _currentBaseKey = key;
  currentBaseLayer = BASE_MAPS[key]().addTo(map);
  // Keep overlays on top: Leaflet re-stacks newly-added layers, but
  // tile layers added later render *above* earlier ones, so set
  // zIndex explicitly to push the base back behind hydro + everything.
  currentBaseLayer.setZIndex(0);
  try {
    localStorage.setItem("bl_basemap", key);
  } catch (_) {
    /* localStorage unavailable; in-memory state still reflects */
  }
  window.currentBaseKey = key;
}

/** Active base-map key. Mirrored to window.currentBaseKey after each
 *  setBaseMap() call so the legacy app.js segmented-control wiring
 *  sees updates without us having to forward setters. */
export function currentBaseKey(): BaseMapKey {
  return _currentBaseKey;
}

// Labeled rivers/streams: free national USGS "Hydro Cached" overlay
// (no key, no deps). Transparent raster designed to sit on a
// basemap. ArcGIS cached tiles are /tile/{level}/{row}/{col} ==
// {z}/{y}/{x}.
export const hydroLayer: L.TileLayer = L.tileLayer(
  "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
  {
    opacity: 0.85,
    maxZoom: 19,
    attribution: "Hydrography &copy; USGS The National Map",
  },
).addTo(map);

// -- Window bridge for legacy app.js -----------------------------------

declare global {
  interface Window {
    L: typeof L;
    map: L.Map;
    setBaseMap: typeof setBaseMap;
    currentBaseKey: BaseMapKey;
    hydroLayer: L.TileLayer;
  }
}

window.map = map;
window.setBaseMap = setBaseMap;
window.currentBaseKey = _currentBaseKey;
window.hydroLayer = hydroLayer;
