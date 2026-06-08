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

import { BASEMAP_TILES_ENABLED, BASEMAP_STYLE_URL } from "./config";
import { ensurePmtilesProtocol } from "./tiles";

type BaseMapKey = "street" | "satellite" | "topo" | "vector";
type RasterBaseKey = "street" | "satellite" | "topo";

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

/** A persisted base key is valid if it's a raster base, or the vector base
 *  when a basemap archive is configured at build time (VITE_BASEMAP_TILES_URL). */
function isBaseKey(k: string): k is BaseMapKey {
  return k in BASES || (k === "vector" && BASEMAP_TILES_ENABLED);
}

function loadBaseMapPref(): BaseMapKey {
  try {
    const v = localStorage.getItem("bl_basemap");
    if (v && isBaseKey(v)) return v;
  } catch (_) {
    /* localStorage unavailable */
  }
  return "street";
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
// A base is "whatever sources+layers sit below the hydro overlay." Raster
// bases are one source + one raster layer; the vector base is the self-hosted
// Protomaps archive's source + its full style-layer stack. We record exactly
// what the current base added so a switch tears down only the base, leaving
// hydro, the overlay sources, and their feature-state intact (we never call
// map.setStyle — see the module header).

let _baseLayerIds: string[] = [];
let _baseSourceIds: string[] = [];
// Bumped on every base change; the async vector add re-checks it after its
// style fetch so a rapid switch-away doesn't paint a now-stale base.
let _baseGen = 0;

/** The base always sits below the hydro overlay (and thus below every module
 *  overlay, which mount on top of hydro). */
function baseAnchor(): string | undefined {
  return map.getLayer("hydro") ? "hydro" : undefined;
}

function addRasterBase(key: RasterBaseKey): void {
  map.addSource("base", BASES[key]);
  map.addLayer({ id: "base", type: "raster", source: "base" }, baseAnchor());
  _baseLayerIds = ["base"];
  _baseSourceIds = ["base"];
}

/** Vector base: fetch the self-hosted Protomaps style.json, point glyphs +
 *  sprite at its R2-hosted assets, then inject its vector source + full layer
 *  stack below the hydro anchor. Async (style fetch), so it guards on _baseGen
 *  to stay correct if the user switches base again before it resolves. */
async function addVectorBase(gen: number): Promise<void> {
  ensurePmtilesProtocol();
  let style: StyleSpecification;
  try {
    style = (await (await fetch(BASEMAP_STYLE_URL)).json()) as StyleSpecification;
  } catch (err) {
    console.warn("vector basemap style failed to load:", err);
    return;
  }
  if (gen !== _baseGen) return; // superseded by a later base switch

  // glyphs/sprite are style-document-level; set them without setStyle so the
  // Protomaps label fonts + icon atlas resolve (the map's default glyphs is a
  // placeholder that lacks the Noto fontstacks the style references).
  if (style.glyphs) map.setGlyphs(style.glyphs);
  // gen_basemap_style emits a single-string sprite; the array form (style-spec
  // SpriteSpecification) isn't used here and setSprite takes a string url.
  if (typeof style.sprite === "string") map.setSprite(style.sprite);

  const before = baseAnchor();
  const layerIds: string[] = [];
  const sourceIds: string[] = [];
  for (const [id, src] of Object.entries(style.sources)) {
    if (!map.getSource(id)) {
      map.addSource(id, src);
      sourceIds.push(id);
    }
  }
  // Insert each layer before the same anchor: sequential inserts preserve the
  // style's own order, ending just below hydro.
  for (const layer of style.layers) {
    if (map.getLayer(layer.id)) continue;
    map.addLayer(layer, before);
    layerIds.push(layer.id);
  }
  _baseLayerIds = layerIds;
  _baseSourceIds = sourceIds;
}

function addBase(key: BaseMapKey): void {
  const gen = ++_baseGen;
  if (key === "vector") void addVectorBase(gen);
  else addRasterBase(key);
}

function removeBase(): void {
  for (const id of _baseLayerIds) if (map.getLayer(id)) map.removeLayer(id);
  for (const id of _baseSourceIds) if (map.getSource(id)) map.removeSource(id);
  _baseLayerIds = [];
  _baseSourceIds = [];
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
  if (!isBaseKey(key) || key === _currentBaseKey) return;
  _currentBaseKey = key;
  removeBase();
  addBase(key); // re-inserted below hydro, overlays untouched
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
  addBase(_currentBaseKey);
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
