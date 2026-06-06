/**
 * Minimal vector basemap theme for the Protomaps basemap schema, hand-written
 * (no extra dependency) so every layer is under our control. That control is
 * the whole point: the same `basemap.pmtiles` file + this same layer list
 * render on web, iOS, and Android via MapLibre, and the file bundles/downloads
 * to the device for fully offline use (read via `file://`) — no per-load tile
 * billing, no vendor lock-in. This web wiring is therefore a working prototype
 * of the mobile offline basemap, not a throwaway visual.
 *
 * Intentionally compact: earth / water / landuse / roads / boundaries + place
 * labels, tuned to recede roads and emphasize water — a fishing map, not a
 * driving map (and a quick taste of the custom-cartography upside over the
 * raster bases). It reads the Protomaps v4 basemap schema: source-layers
 * `earth`, `water`, `landuse`, `roads`, `boundaries`, `places`, keyed off the
 * `kind` / `name` properties. The archive is produced by
 * scripts/build_basemap_tiles.sh (a `pmtiles extract` of a region).
 */

import { LayerSpecification } from "maplibre-gl";

// Protomaps publishes hosted fonts + sprites. For the web PoC we read them
// straight from that CDN; for an offline mobile build these get bundled on the
// device alongside the .pmtiles file (they're a few hundred KB).
export const BASEMAP_GLYPHS =
  "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf";
export const BASEMAP_SPRITE =
  "https://protomaps.github.io/basemaps-assets/sprites/v4/light";

// Muted, water-forward palette. Land recedes; water reads first.
const C = {
  earth: "#f6f4ee",
  water: "#a9cce3",
  park: "#e3ecd9",
  roadMinor: "#ece7da",
  roadMajor: "#ddd5c2",
  boundary: "#c9bcd4",
  text: "#5a5546",
  textHalo: "#f6f4ee",
};

/** Build the base layer stack against a vector `source` (id "base"). */
export function basemapLayers(source: string): LayerSpecification[] {
  const layers = [
    {
      id: "base-earth",
      type: "fill",
      source,
      "source-layer": "earth",
      paint: { "fill-color": C.earth },
    },
    {
      id: "base-landuse",
      type: "fill",
      source,
      "source-layer": "landuse",
      filter: [
        "in",
        ["get", "kind"],
        ["literal", ["park", "forest", "wood", "nature_reserve", "grass", "recreation_ground"]],
      ],
      paint: { "fill-color": C.park, "fill-opacity": 0.7 },
    },
    {
      id: "base-water",
      type: "fill",
      source,
      "source-layer": "water",
      paint: { "fill-color": C.water },
    },
    {
      id: "base-roads-minor",
      type: "line",
      source,
      "source-layer": "roads",
      filter: ["in", ["get", "kind"], ["literal", ["minor_road", "other", "path"]]],
      paint: {
        "line-color": C.roadMinor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 11, 0.4, 16, 2],
      },
    },
    {
      id: "base-roads-major",
      type: "line",
      source,
      "source-layer": "roads",
      filter: ["in", ["get", "kind"], ["literal", ["highway", "major_road", "medium_road"]]],
      paint: {
        "line-color": C.roadMajor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 7, 0.6, 16, 4],
      },
    },
    {
      id: "base-boundaries",
      type: "line",
      source,
      "source-layer": "boundaries",
      paint: {
        "line-color": C.boundary,
        "line-width": 0.8,
        "line-dasharray": [3, 2],
      },
    },
    {
      id: "base-places",
      type: "symbol",
      source,
      "source-layer": "places",
      filter: ["in", ["get", "kind"], ["literal", ["locality", "city", "town", "village"]]],
      layout: {
        "text-field": ["coalesce", ["get", "name"], ""],
        "text-font": ["Noto Sans Regular"],
        "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 14, 16],
      },
      paint: {
        "text-color": C.text,
        "text-halo-color": C.textHalo,
        "text-halo-width": 1.4,
      },
    },
  ];
  // The MapLibre style-spec expression types don't narrow from object literals;
  // cast the whole stack like streams.ts does with its layer specs.
  return layers as unknown as LayerSpecification[];
}

/** Layer ids this theme contributes, in stack order (for teardown). */
export const BASEMAP_LAYER_IDS = [
  "base-earth",
  "base-landuse",
  "base-water",
  "base-roads-minor",
  "base-roads-major",
  "base-boundaries",
  "base-places",
];
