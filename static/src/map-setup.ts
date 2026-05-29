/**
 * MapLibre GL JS map initialization + base-map management. Replaces the
 * Leaflet map-setup (PR B2).
 *
 * Owns:
 *   - the singleton MapLibre map instance (`map`)
 *   - the three raster base providers (CARTO Light / Esri Satellite /
 *     USGS Topographic) + the USGS Hydro Cached raster overlay, all as
 *     raster sources/layers
 *   - active base-map state + bl_basemap localStorage persistence
 *   - a "map ready" gate (onMapReady / mapReady) — MapLibre rejects
 *     addSource/addLayer/setData before the style `load` fires, so every
 *     overlay module registers its source+layer setup via onMapReady
 *   - riverBySite: the site_no -> River registry that replaces Leaflet's
 *     `_blRiver` layer property for river-line click resolution
 *   - getGeoJSON(id): typed GeoJSONSource accessor for setData callers
 *
 * Base-map switching swaps ONLY the `base` source/layer (re-inserted
 * below the `hydro` layer). We never call map.setStyle, so overlay
 * sources, their data, and feature-state survive a base change — no
 * style.load re-attach dance needed.
 *
 * Coordinate order: MapLibre is [lng, lat]. All coordinates flow through
 * helpers in coords.ts.
 */

import maplibregl, {
  Map as MaplibreMap,
  GeoJSONSource,
  RasterSourceSpecification,
  StyleSpecification,
} from "maplibre-gl";

type BaseMapKey = "street" | "satellite" | "topo";

const BASES: Record<BaseMapKey, RasterSourceSpecification> = {
  street: {
    type: "raster",
    tiles: [
      "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      "https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      "https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
      "https://d.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png",
    ],
    tileSize: 256,
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO",
    maxzoom: 19,
  },
  satellite: {
    type: "raster",
    tiles: [
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    ],
    tileSize: 256,
    attribution: "Source: Esri, Maxar, Earthstar Geographics",
    maxzoom: 19,
  },
  topo: {
    type: "raster",
    tiles: [
      "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
    ],
    tileSize: 256,
    attribution: "USGS The National Map",
    maxzoom: 16,
  },
};

const HYDRO: RasterSourceSpecification = {
  type: "raster",
  tiles: [
    "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
  ],
  tileSize: 256,
  attribution: "Hydrography &copy; USGS The National Map",
  maxzoom: 19,
};

function loadBaseMapPref(): BaseMapKey {
  try {
    const v = localStorage.getItem("bl_basemap");
    return v && v in BASES ? (v as BaseMapKey) : "street";
  } catch (_) {
    return "street";
  }
}

let _currentBaseKey: BaseMapKey = loadBaseMapPref();

// Empty style at construction; base/hydro (and module overlays) are added
// on the `load` event, where addSource/addLayer are legal.
const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [],
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
};

export const map: MaplibreMap = new maplibregl.Map({
  container: "map",
  style: EMPTY_STYLE,
  center: [-76.6, 39.0], // [lng, lat]; init() jumpTo's the real state center
  zoom: 7,
  attributionControl: false, // the chrome supplies a static attribution
});

// site_no -> River, populated by rivers.ts. Replaces the `_blRiver`
// property Leaflet layers carried; line clicks read feature props.site_no
// and look the river up here.
export const riverBySite = new Map<string, River>();

/** Typed accessor for a GeoJSON source (null until the map is ready and
 *  the owning module has added it). */
export function getGeoJSON(id: string): GeoJSONSource | null {
  if (!map.getSource(id)) return null;
  return map.getSource(id) as GeoJSONSource;
}

// -- Base + hydro wiring ---------------------------------------------

function addBaseLayer(key: BaseMapKey): void {
  map.addSource("base", BASES[key]);
  // Insert below hydro if it exists, so the base always sits at the
  // bottom of the stack.
  const before = map.getLayer("hydro") ? "hydro" : undefined;
  map.addLayer({ id: "base", type: "raster", source: "base" }, before);
}

function addHydroLayer(): void {
  map.addSource("hydro", HYDRO);
  map.addLayer({
    id: "hydro",
    type: "raster",
    source: "hydro",
    layout: { visibility: _hydroVisible ? "visible" : "none" },
    paint: { "raster-opacity": 0.85 },
  });
}

export function setBaseMap(key: BaseMapKey): void {
  if (!BASES[key] || key === _currentBaseKey) return;
  _currentBaseKey = key;
  if (map.getLayer("base")) map.removeLayer("base");
  if (map.getSource("base")) map.removeSource("base");
  addBaseLayer(key); // re-inserted below hydro, overlays untouched
  try {
    localStorage.setItem("bl_basemap", key);
  } catch (_) {
    /* localStorage unavailable; in-memory state still reflects */
  }
  window.currentBaseKey = key;
}

export function currentBaseKey(): BaseMapKey {
  return _currentBaseKey;
}

let _hydroVisible = true; // lyr-usgs default checked

/** Toggle the USGS hydro overlay (the lyr-usgs checkbox). Safe before
 *  the map is ready: the desired state is applied when the layer mounts. */
export function setHydroVisible(on: boolean): void {
  _hydroVisible = on;
  if (map.getLayer("hydro")) {
    map.setLayoutProperty("hydro", "visibility", on ? "visible" : "none");
  }
}

// -- Ready gate ------------------------------------------------------
// addSource/addLayer/setData throw before the style `load` fires. Overlay
// modules register their setup here; it runs once the base + hydro exist.

let _ready = false;
const _readyCbs: Array<() => void> = [];

export function onMapReady(cb: () => void): void {
  if (_ready) cb();
  else _readyCbs.push(cb);
}

export function mapReady(): Promise<void> {
  return new Promise((resolve) => onMapReady(resolve));
}

map.on("load", () => {
  addBaseLayer(_currentBaseKey);
  addHydroLayer();
  _ready = true;
  for (const cb of _readyCbs) cb();
  _readyCbs.length = 0;
});

// -- Window bridge ---------------------------------------------------

declare global {
  interface Window {
    map: MaplibreMap;
    setBaseMap: typeof setBaseMap;
    currentBaseKey: BaseMapKey;
  }
}

window.map = map;
window.setBaseMap = setBaseMap;
window.currentBaseKey = _currentBaseKey;
