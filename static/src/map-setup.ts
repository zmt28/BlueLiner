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

import {
  BASEMAP_TILES_ENABLED,
  BASEMAP_TILES_URL,
  BASEMAP_STYLE_URL,
} from "./config";
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
  // M1.1b: the self-hosted vector base is the default wherever it's
  // configured (sharper on HiDPI, self-hosted, offline-capable); the
  // raster street base remains the fallback default when it isn't.
  return BASEMAP_TILES_ENABLED ? "vector" : "street";
}

let _currentBaseKey: BaseMapKey = loadBaseMapPref();

// Empty style at construction; base/hydro (and module overlays) are added
// on the `load` event, where addSource/addLayer are legal.
// Glyphs: self-hosted basemap fonts when configured (so text layers —
// stream labels — work on EVERY base with zero third-party dependency);
// the demotiles placeholder remains only for unconfigured dev builds.
const EMPTY_STYLE: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [],
  glyphs: BASEMAP_TILES_ENABLED
    ? BASEMAP_TILES_URL.replace(
        /basemap\.pmtiles$/,
        "basemap/fonts/{fontstack}/{range}.pbf",
      )
    : "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
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

/** The base always sits below the hillshade + hydro overlays (and thus
 *  below every module overlay, which mount on top of hydro). */
function baseAnchor(): string | undefined {
  if (map.getLayer("hillshade")) return "hillshade";
  return map.getLayer("hydro") ? "hydro" : undefined;
}

function addRasterBase(key: RasterBaseKey, gen: number): void {
  // Generation-unique ids so a crossfading old base and its replacement
  // can coexist below hydro until the new one has painted (M2.e2).
  const id = `base-${gen}`;
  map.addSource(id, BASES[key]);
  map.addLayer({ id, type: "raster", source: id }, baseAnchor());
  _baseLayerIds = [id];
  _baseSourceIds = [id];
}

/** Vector base: fetch the self-hosted Protomaps style.json, point glyphs +
 *  sprite at its R2-hosted assets, then inject its vector source + full layer
 *  stack below the hydro anchor. Async (style fetch), so it guards on _baseGen
 *  to stay correct if the user switches base again before it resolves. */
async function addVectorBase(
  gen: number,
): Promise<"ok" | "failed" | "superseded"> {
  ensurePmtilesProtocol();
  let style: StyleSpecification;
  try {
    style = (await (await fetch(BASEMAP_STYLE_URL)).json()) as StyleSpecification;
  } catch (err) {
    console.warn("vector basemap style failed to load:", err);
    return "failed";
  }
  if (gen !== _baseGen) return "superseded"; // a later base switch won

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
  return "ok";
}

/** Boot-time base add (no previous base to crossfade from). If the
 *  persisted vector base fails its style fetch, fall back to the street
 *  raster rather than booting with no base at all. */
function addBase(key: BaseMapKey): void {
  const gen = ++_baseGen;
  if (key === "vector") {
    void addVectorBase(gen).then((r) => {
      if (r === "failed" && gen === _baseGen && !_baseLayerIds.length) {
        _currentBaseKey = "street";
        window.currentBaseKey = "street";
        addRasterBase("street", gen);
        applyHydroVisibility(); // raster base -> hydro follows the checkbox again
        announceBase();
      }
    });
  } else {
    addRasterBase(key, gen);
  }
}

// -- Terrain hillshade (M3.1) -----------------------------------------
// AWS Open Data Terrarium elevation tiles (keyless, free) rendered as a
// subtle hillshade between the base and hydro. Anglers read gradient;
// this is the single biggest "reads like a real topo product" addition
// at zero cost. Toggled by the lyr-terrain checkbox (default on).

const TERRAIN_DEM_TILES =
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

let _terrainVisible = true; // lyr-terrain default checked

function addHillshadeLayer(): void {
  map.addSource("terrain-dem", {
    type: "raster-dem",
    tiles: [TERRAIN_DEM_TILES],
    encoding: "terrarium",
    tileSize: 256,
    maxzoom: 15,
    attribution: "Terrain: Mapzen (AWS Open Data)",
  });
  map.addLayer({
    id: "hillshade",
    type: "hillshade",
    source: "terrain-dem",
    layout: { visibility: _terrainVisible ? "visible" : "none" },
    paint: {
      // Subtle: shading should read as texture under the data layers,
      // not compete with them (labels sit below on the vector base).
      "hillshade-exaggeration": 0.3,
      "hillshade-shadow-color": "#5a5145",
      "hillshade-highlight-color": "#ffffff",
    },
  });
}

/** Toggle the terrain hillshade (the lyr-terrain checkbox). Safe before
 *  the map is ready: the desired state is applied when the layer mounts. */
export function setTerrainVisible(on: boolean): void {
  _terrainVisible = on;
  if (map.getLayer("hillshade")) {
    map.setLayoutProperty("hillshade", "visibility", on ? "visible" : "none");
  }
}

function addHydroLayer(): void {
  map.addSource("hydro", HYDRO);
  map.addLayer({
    id: "hydro",
    type: "raster",
    source: "hydro",
    layout: { visibility: hydroEffective() ? "visible" : "none" },
    paint: { "raster-opacity": 0.85 },
  });
}

// M1.4: a hidden background layer added just above hydro. Line overlays
// (public lands, trails, clickable streams) insert BEFORE this anchor and
// symbol/point overlays append after it, so z-order is an explicit
// contract instead of a registration-order + promise-timing accident.
const SYMBOL_ANCHOR = "bl-anchor-symbols";

function addSymbolAnchor(): void {
  map.addLayer({
    id: SYMBOL_ANCHOR,
    type: "background",
    layout: { visibility: "none" },
    paint: {},
  });
}

/** beforeId for line overlays: below every symbol layer, above hydro.
 *  Undefined before the map is ready (callers all run via onMapReady). */
export function lineOverlayAnchor(): string | undefined {
  return map.getLayer(SYMBOL_ANCHOR) ? SYMBOL_ANCHOR : undefined;
}

export function setBaseMap(key: BaseMapKey): void {
  if (!isBaseKey(key) || key === _currentBaseKey) return;
  const prevKey = _currentBaseKey;
  _currentBaseKey = key;
  // Crossfade (M2.e2): add the new base below hydro FIRST and retire the
  // old one only after the new one has painted. Removing first flashed
  // the gray map background between raster bases and, for the async
  // vector base, left no base at all for the whole style round-trip.
  const oldLayers = _baseLayerIds;
  const oldSources = _baseSourceIds;
  _baseLayerIds = [];
  _baseSourceIds = [];
  const removeOld = () => {
    for (const id of oldLayers) if (map.getLayer(id)) map.removeLayer(id);
    for (const id of oldSources) if (map.getSource(id)) map.removeSource(id);
  };
  // Retire the old base when the new one has painted: its source reports
  // loaded, or the map goes idle — with a hard timeout so the old base
  // can never leak when tiles hang (slow network, offline, tile outage).
  const retireOldWhenPainted = (newSourceId?: string) => {
    let done = false;
    const onSrc = (e: { sourceId?: string; isSourceLoaded?: boolean }) => {
      if (e.sourceId === newSourceId && e.isSourceLoaded) fire();
    };
    const fire = () => {
      if (done) return;
      done = true;
      map.off("sourcedata", onSrc);
      removeOld();
    };
    if (newSourceId) map.on("sourcedata", onSrc);
    map.once("idle", fire);
    window.setTimeout(fire, 4000);
  };
  const gen = ++_baseGen;
  if (key === "vector") {
    void addVectorBase(gen).then((r) => {
      if (r === "ok") {
        retireOldWhenPainted();
      } else if (r === "failed" && gen === _baseGen) {
        // Style fetch failed and nothing newer superseded us: keep the
        // previous base on screen and restore its tracking + key so the
        // UI reflects what's actually shown.
        _baseLayerIds = oldLayers;
        _baseSourceIds = oldSources;
        _currentBaseKey = prevKey;
        window.currentBaseKey = prevKey;
        applyHydroVisibility();
        announceBase();
      } else {
        removeOld(); // superseded: a newer base is on top; drop ours
      }
    });
  } else {
    addRasterBase(key, gen);
    retireOldWhenPainted(`base-${gen}`);
  }
  applyHydroVisibility(); // hydro suppressed on vector, checkbox-driven on raster
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

/** Announce the active base key so UI (segment buttons, hydro toggle)
 *  can resync after programmatic changes: the boot fallback when a
 *  persisted vector base fails, and the failed-switch revert. */
function announceBase(): void {
  document.dispatchEvent(
    new CustomEvent("bl:base-changed", { detail: _currentBaseKey }),
  );
}

let _hydroVisible = true; // lyr-usgs default checked

/** M1.2: the hydro raster is suppressed while the vector base is active —
 *  the vector base carries its own hydrography, and the 0.85-opacity
 *  raster double-draws water and paints over the vector labels. The
 *  checkbox state (`_hydroVisible`) is preserved; it re-applies whenever
 *  a raster base is active. */
function hydroEffective(): boolean {
  return _hydroVisible && _currentBaseKey !== "vector";
}

function applyHydroVisibility(): void {
  if (map.getLayer("hydro")) {
    map.setLayoutProperty(
      "hydro",
      "visibility",
      hydroEffective() ? "visible" : "none",
    );
  }
}

/** Toggle the USGS hydro overlay (the lyr-usgs checkbox). Safe before
 *  the map is ready: the desired state is applied when the layer mounts. */
export function setHydroVisible(on: boolean): void {
  _hydroVisible = on;
  applyHydroVisibility();
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
  addHillshadeLayer(); // base < hillshade < hydro < anchor (M1.4/M3.1)
  addHydroLayer();
  addSymbolAnchor();
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
