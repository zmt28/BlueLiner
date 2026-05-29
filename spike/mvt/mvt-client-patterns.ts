/**
 * MapLibre vector-tile client patterns (MVT spike) — REFERENCE ONLY.
 *
 * Not imported by the app (lives under spike/, outside the tsconfig include).
 * Shows how streams.ts / map-layers.ts change when the clickable-stream
 * network moves from a GeoJSON source to a VECTOR-TILE source. The styling,
 * the two-layer visible+hit pattern, the color-mode toggle, the click flow,
 * and the feature-state highlight all carry over from B2 unchanged — only the
 * SOURCE and a `source-layer` differ, and a few setData/zoom-gate/bbox-cap
 * workarounds disappear.
 *
 * Path A (recommended): static PMTiles on R2, read via the pmtiles protocol.
 * Path B (fallback): a dynamic /tiles/streams/{z}/{x}/{y}.pbf endpoint.
 */

import maplibregl, { ExpressionSpecification, Map as MaplibreMap } from "maplibre-gl";
// Path A only: register the pmtiles:// protocol (npm i pmtiles)
// import { Protocol } from "pmtiles";

const SOURCE_LAYER = "streams"; // the MVT layer name baked by tippecanoe / the encoder

// ---- Source: pick ONE ----------------------------------------------------

/** Path A — static PMTiles on R2 (a CDN with HTTP range support). */
export function addStreamSourcePMTiles(map: MaplibreMap): void {
  // const protocol = new Protocol();
  // maplibregl.addProtocol("pmtiles", protocol.tile);
  map.addSource("clickable-streams", {
    type: "vector",
    url: "pmtiles://https://data.blueliner.app/v1/streams.pmtiles",
    promoteId: "levelpathid", // stable per-reach id -> feature-state highlight
  });
}

/** Path B — dynamic Python MVT endpoint (no PostGIS; reuses the GiST query). */
export function addStreamSourceDynamic(map: MaplibreMap): void {
  map.addSource("clickable-streams", {
    type: "vector",
    tiles: [`${location.origin}/tiles/streams/{z}/{x}/{y}.pbf`],
    minzoom: 6,
    maxzoom: 14, // overzoom past 14 on the client
    promoteId: "levelpathid",
  });
}

// ---- Layers: identical to the B2 GeoJSON version + a `source-layer` -------

const TROUT_COLOR_MATCH = [
  "match",
  ["get", "trout_class"],
  "class_a", "#b8860b",
  "wilderness", "#117a65",
  "wild_reproduction", "#1e8449",
  "stocked", "#2c6fbf",
  "designated", "#27ae60",
  "#8a9bb0",
] as unknown as ExpressionSpecification;

function colorExpr(mode: "trout" | "conditions"): ExpressionSpecification {
  const base = mode === "conditions" ? "#9aa7b8" : TROUT_COLOR_MATCH;
  return [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    "#e74c3c",
    base,
  ] as unknown as ExpressionSpecification;
}

export function addStreamLayers(map: MaplibreMap, mode: "trout" | "conditions"): void {
  map.addLayer({
    id: "clickable-streams",
    type: "line",
    source: "clickable-streams",
    "source-layer": SOURCE_LAYER, // <-- the only structural addition vs GeoJSON
    layout: { "line-cap": "round" },
    paint: {
      "line-color": colorExpr(mode),
      "line-width": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        8,
        ["interpolate", ["linear"], ["coalesce", ["get", "streamorder"], 3], 1, 4, 7, 7],
      ] as unknown as ExpressionSpecification,
      "line-opacity": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        0.95,
        0.8,
      ] as unknown as ExpressionSpecification,
    },
  });
  // Transparent fat casing for touch — same pattern, same source + source-layer.
  map.addLayer({
    id: "clickable-streams-hit",
    type: "line",
    source: "clickable-streams",
    "source-layer": SOURCE_LAYER,
    paint: { "line-color": "#000", "line-opacity": 0, "line-width": 16 },
  });
  // Click + color-mode (setPaintProperty) + gauged/ungauged routing are
  // byte-for-byte the B2 streams.ts code; e.features[0].properties is identical.
}

// ---- Highlight across tiles ---------------------------------------------
// feature-state persists per loaded tile. A reach crossing a tile boundary is
// multiple features sharing `levelpathid`, so highlighting by id covers all
// parts. As the user pans, new tiles arrive without the selected state — so
// re-apply on "sourcedata" (the tile analogue of B2's re-apply-after-setData).

export function wireHighlightReapply(
  map: MaplibreMap,
  getKey: () => { name: string | null; lpid: number | null } | null,
  applyHighlight: (key: { name: string | null; lpid: number | null }) => void,
): void {
  map.on("sourcedata", (e) => {
    if (e.sourceId !== "clickable-streams" || !e.isSourceLoaded) return;
    const key = getKey();
    if (key) applyHighlight(key); // querySourceFeatures + setFeatureState per match
  });
}

// ---- What goes AWAY once parity is confirmed -----------------------------
//   - the zoom-9 gate + the 4° bbox cap (tiles work at every zoom)
//   - the moveend-debounced loadClickableStreams() refetch
//   - source.setData() plumbing + the manual _loadedClkNames/_loadedClkLpids
//     viewport bookkeeping (queryRenderedFeatures replaces it for
//     _riverHasClickableReach)
void addStreamSourcePMTiles;
void addStreamSourceDynamic;
void addStreamLayers;
void wireHighlightReapply;
