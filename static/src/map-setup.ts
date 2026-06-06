/**
 * MapLibre GL JS map initialization + base-map management. Replaces the
 * Leaflet map-setup (PR B2).
 *
 * Owns:
 *   - the singleton MapLibre map instance (`map`)
 *   - the three raster base providers (CARTO Light / Esri Satellite /
 *     USGS Topographic) + the USGS Hydro Cached raster overlay, all as
 *     raster sources/layers, plus an optional self-hosted *vector* base
 *     (Protomaps PMTiles, see basemap.ts) that appears only when
 *     VITE_BASEMAP_TILES_URL is configured — the same file a mobile build
 *     bundles for offline use
 *   - active base-map state + bl_basemap localStorage persistence
 *   - a "map ready" gate (onMapReady / mapReady) — MapLibre rejects
 *     addSource/addLayer/setData before the style `load` fires, so every
 *     overlay module registers its source+layer setup via onMapReady
 *   - riverBySite: the site_no -> River registry that replaces Leaflet's
 *     `_blRiver` layer property for river-line click resolution
 *   - getGeoJSON(id): typed GeoJSONSource accessor for setData callers
 *
 * Base-map switching swaps ONLY the `base` source and its layer(s) —
 * one raster layer, or the vector base's theme stack — re-inserted below
 * the `hydro` layer. We never call map.setStyle, so overlay sources,
 * their data, and feature-state survive a base change — no style.load
 * re-attach dance needed.
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
import { BASEMAP_TILES_ENABLED, BASEMAP_TILES_URL } from "./config";
import { basemapLayers, BASEMAP_GLYPHS, BASEMAP_SPRITE } from "./basemap";
import { ensurePmtilesProtocol } from "./tiles";

// The three raster bases plus an optional vector base. The vector base is a
// self-hosted Protomaps PMTiles archive (the same file a mobile build bundles
// for offline use); it only appears when VITE_BASEMAP_TILES_URL is configured.
const RASTER_KEYS = ["street", "satellite", "topo"] as const;
type RasterBaseKey = (typeof RASTER_KEYS)[number];
type BaseMapKey = RasterBaseKey | "vector";

function isValidBase(k: string): k is BaseMapKey {
  if (k === "vector") return BASEMAP_TILES_ENABLED;
  return (RASTER_KEYS as readonly string[]).includes(k);
}

const BASES: Record<RasterBaseKey, RasterSourceSpecification> = {
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
    return v && isValidBase(v) ? v : "street";
  } catch (_) {
    return "street";
  }
}

let _currentBaseKey: BaseMapKey = loadBaseMapPref();

// Empty style at construction; base/hydro (and module overlays) are added
// on the `load` event, where addSource/addLayer are legal.
// Glyphs + sprite point at the Protomaps basemap assets so the vector base's
// labels have fonts. They're harmless when the base stays raster (nothing
// renders text then); for offline these get bundled on-device.
const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [],
  glyphs: BASEMAP_GLYPHS,
  sprite: BASEMAP_SPRITE,
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

// The layer ids currently making up the base, in stack order. Raster bases
// are a single "base" layer; the vector base is the Protomaps theme stack.
// Tracked so a base swap removes exactly what it added.
let _baseLayerIds: string[] = [];

function addBaseLayer(key: BaseMapKey): void {
  // Insert below hydro if it exists, so the base always sits at the
  // bottom of the stack. Each vector layer is inserted before hydro too,
  // which preserves their relative order (earth → water → roads → labels).
  const before = map.getLayer("hydro") ? "hydro" : undefined;
  if (key === "vector") {
    ensurePmtilesProtocol();
    map.addSource("base", {
      type: "vector",
      url: `pmtiles://${BASEMAP_TILES_URL}`,
    });
    const layers = basemapLayers("base");
    for (const layer of layers) map.addLayer(layer, before);
    _baseLayerIds = layers.map((l) => l.id);
    return;
  }
  map.addSource("base", BASES[key]);
  map.addLayer({ id: "base", type: "raster", source: "base" }, before);
  _baseLayerIds = ["base"];
}

function removeBaseLayers(): void {
  for (const id of _baseLayerIds) {
    if (map.getLayer(id)) map.removeLayer(id);
  }
  if (map.getSource("base")) map.removeSource("base");
  _baseLayerIds = [];
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
  if (!isValidBase(key) || key === _currentBaseKey) return;
  _currentBaseKey = key;
  removeBaseLayers();
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
