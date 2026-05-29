/**
 * MapLibre GL JS spike (PR B2 prep). NOT a production module --
 * reference TypeScript that exercises every pattern Blueliner uses
 * via Leaflet today, mapped to the MapLibre API. Each section is a
 * concrete answer for "how do we replace L.X with maplibregl.Y".
 *
 * Run: this file isn't imported by main.ts. It's a design document
 * + compilation check. Validates that:
 *
 *   1. maplibre-gl's TS types are sufficient for our patterns
 *   2. The bundle compiles cleanly
 *   3. Each Leaflet API has a clean replacement
 *
 * What's NOT in here: actual runtime testing in a browser. That
 * happens after B2a lands the foundation and we can compare real
 * render performance side-by-side.
 */

import maplibregl, {
  Map as MaplibreMap,
  MapMouseEvent,
  Popup,
  Marker,
  GeoJSONSource,
  LngLatBoundsLike,
  StyleSpecification,
  RasterSourceSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// ====================================================================
// Pattern 1: Map setup -- L.map() -> new maplibregl.Map()
// ====================================================================
//
// Style.json approach:
//   - Build a minimal style with our existing raster XYZ tile URLs
//     wrapped as raster sources. No vector tile decision needed for
//     B2 -- defer that to a Phase 3.
//   - sources: { street, satellite, topo, hydro }
//   - layers: one for the active base + one for the hydro overlay
//
// Init pattern that mirrors map-setup.ts:

function buildRasterStyle(baseKey: "street" | "satellite" | "topo"): StyleSpecification {
  const BASES: Record<typeof baseKey, RasterSourceSpecification> = {
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
  return {
    version: 8,
    sources: {
      base: BASES[baseKey],
      hydro: {
        type: "raster",
        tiles: [
          "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        attribution: "Hydrography &copy; USGS The National Map",
        maxzoom: 19,
      },
    },
    layers: [
      { id: "base", type: "raster", source: "base" },
      { id: "hydro", type: "raster", source: "hydro", paint: { "raster-opacity": 0.85 } },
    ],
  };
}

export function setupMap(): MaplibreMap {
  return new maplibregl.Map({
    container: "map",
    style: buildRasterStyle("street"),
    center: [-76.6122, 39.2904], // [lng, lat] -- MapLibre flips Leaflet's [lat, lng]!
    zoom: 7,
    attributionControl: { compact: true },
  });
}

// FINDING #1 (critical): MapLibre uses [lng, lat] in all coordinate
// arrays. Leaflet uses [lat, lng]. Every map.setView, marker, geometry
// reference needs the flip. This is THE most common B2 footgun --
// every coordinate site is a potential bug.

// ====================================================================
// Pattern 2: Switching base maps -- setBaseMap(key)
// ====================================================================
//
// MapLibre doesn't have "remove this tile layer" since base styles are
// declarative. The cleanest path: rebuild the style with the new base
// source/layer. Alternative: keep all three base sources loaded and
// toggle which layer is visible.

export function setBaseMap(
  map: MaplibreMap,
  key: "street" | "satellite" | "topo",
): void {
  // Replace just the base source. Hydro + overlays stay because they
  // were added as separate sources.
  map.setStyle(buildRasterStyle(key), { diff: true });
  // FINDING #2: setStyle with diff:true is fast (just updates the
  // diff) BUT it drops everything added via map.addSource/addLayer
  // post-init unless we re-add in a `style.load` listener. So our
  // overlays (river-lines, clickable streams, etc.) need to be
  // re-attached after every base swap. Concrete pattern below.
}

// ====================================================================
// Pattern 3: GeoJSON layers -- L.geoJSON(data, {style, onEachFeature})
// ====================================================================
//
// MapLibre's data-driven styling is fundamentally different. Instead
// of a per-feature style callback, paint properties read from feature
// properties via expressions. For static styling this is fine; for
// the trout/conditions toggle (where the SAME feature's color depends
// on a UI-mode flag) we need either filter-based layering or expression-
// based property reads.

export function addRiverLinesLayer(map: MaplibreMap): void {
  map.addSource("river-lines", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });
  map.addLayer({
    id: "river-lines",
    type: "line",
    source: "river-lines",
    paint: {
      // Per-feature color from properties.color (the existing
      // /api/river_lines response shape works as-is).
      "line-color": ["coalesce", ["get", "color"], "#2c6fbf"],
      "line-width": 5,
      "line-opacity": 0.6,
    },
  });
}

// FINDING #3: GeoJSON layers in MapLibre re-render when you call
// `source.setData(newGeoJSON)`. Reasonable perf for our scale
// (700k features in clickable streams at worst, viewport-bounded
// to ~5-10k). MUCH faster than Leaflet's L.geoJSON.addData which
// creates a DOM <path> per feature -- MapLibre paints all features
// in one GL draw call.

// ====================================================================
// Pattern 4: Color-mode toggle for clickable streams (trout / conditions)
// ====================================================================
//
// Existing streams.ts does setStreamColorMode + restyleStreams which
// re-applies a JS callback to every layer. In MapLibre we either:
//
//   (a) Use a data-driven expression that reads BOTH the trout_class
//       property AND a global state (via setPaintProperty when mode
//       changes); or
//   (b) Two layers with opposite filters; toggle visibility on mode
//       change.
//
// Option (a) is cleaner. Implementation:

export function addClickableStreamsLayer(map: MaplibreMap): void {
  map.addSource("clickable-streams", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
    // promoteId lets feature-state work without needing real ids in
    // the source data. levelpathid is a stable per-reach identifier.
    promoteId: "levelpathid",
  });

  // VISIBLE layer -- styled per trout class OR greyed by conditions
  // mode. We use a "step" expression on a top-level paint property
  // that we'll mutate via setPaintProperty when the mode changes.
  map.addLayer({
    id: "clickable-streams-visible",
    type: "line",
    source: "clickable-streams",
    paint: {
      "line-color": [
        "match",
        ["get", "trout_class"],
        "class_a", "#b8860b",
        "wilderness", "#117a65",
        "wild_reproduction", "#1e8449",
        "stocked", "#2c6fbf",
        "designated", "#27ae60",
        "#8a9bb0", // fallback
      ],
      "line-width": [
        "interpolate",
        ["linear"],
        ["get", "streamorder"],
        1, 4,
        7, 7,
      ],
      "line-opacity": 0.8,
    },
    // Layout property for non-interactive (Leaflet's `interactive:false`)
  });

  // HIT layer -- transparent fat casing for touch. Order matters:
  // hit layer ABOVE visible so click events hit the wider one.
  map.addLayer({
    id: "clickable-streams-hit",
    type: "line",
    source: "clickable-streams",
    paint: {
      "line-color": "#000",
      "line-opacity": 0,
      "line-width": 16,
    },
  });
}

export function setStreamColorMode(
  map: MaplibreMap,
  mode: "trout" | "conditions",
): void {
  if (mode === "conditions") {
    // Conditions mode: grey the network so gauged condition markers
    // read on top.
    map.setPaintProperty("clickable-streams-visible", "line-color", "#9aa7b8");
  } else {
    // Restore trout-class palette.
    map.setPaintProperty("clickable-streams-visible", "line-color", [
      "match",
      ["get", "trout_class"],
      "class_a", "#b8860b",
      "wilderness", "#117a65",
      "wild_reproduction", "#1e8449",
      "stocked", "#2c6fbf",
      "designated", "#27ae60",
      "#8a9bb0",
    ]);
  }
}

// FINDING #4: setPaintProperty is O(1) -- MapLibre just updates the
// expression, no re-walking the features. Much cleaner than Leaflet's
// .setStyle(fn) which iterates every Layer in the GeoJSON group.

// ====================================================================
// Pattern 5: Click handlers -- feature.on('click') -> map.on('click', layerId)
// ====================================================================
//
// Leaflet: each feature has its own click listener. MapLibre: one
// click listener per LAYER. The event handler receives e.features,
// the array of features under the cursor at that pixel.

export function wireStreamClicks(
  map: MaplibreMap,
  onClick: (props: ClickableStreamProps, lngLat: maplibregl.LngLat) => void,
): void {
  map.on("click", "clickable-streams-hit", (e: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
    if (!e.features || !e.features.length) return;
    const feat = e.features[0];
    onClick(feat.properties as ClickableStreamProps, e.lngLat);
  });
  // Cursor affordance.
  map.on("mouseenter", "clickable-streams-hit", () => {
    map.getCanvas().style.cursor = "pointer";
  });
  map.on("mouseleave", "clickable-streams-hit", () => {
    map.getCanvas().style.cursor = "";
  });
}

// ====================================================================
// Pattern 6: Selection highlight -- setStyle(layer, {...}) -> feature-state
// ====================================================================
//
// Leaflet selection: clickableVisible.eachLayer + l.setStyle on
// matching features (re-applied after every fetch since the layer
// gets recreated). MapLibre: feature-state is persistent across
// source.setData calls when promoteId is set. Set selected:true on
// the matching features, drive paint via a case expression.

interface SelStreamKey {
  name: string | null;
  lpid: number | null;
}

function applyStreamHighlight(
  map: MaplibreMap,
  features: maplibregl.MapGeoJSONFeature[],
  key: SelStreamKey,
): void {
  // Clear previous selection.
  map.removeFeatureState({ source: "clickable-streams" });
  // Apply to all matching features.
  for (const f of features) {
    const props = f.properties as ClickableStreamProps;
    const nameMatch = key.name
      ? (props.gnis_name || "").trim().toLowerCase() === key.name
      : false;
    const lpidMatch = key.lpid != null && props.levelpathid === key.lpid;
    if (nameMatch || lpidMatch) {
      map.setFeatureState(
        { source: "clickable-streams", id: f.id },
        { selected: true },
      );
    }
  }
  // Update the paint expression to honor feature-state. Done once at
  // layer-add time; the case expression evaluates per-feature on
  // every paint.
  map.setPaintProperty("clickable-streams-visible", "line-color", [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    "#e74c3c", // selected color
    /* otherwise the existing per-class expression */
    ["match",
      ["get", "trout_class"],
      "class_a", "#b8860b",
      "wilderness", "#117a65",
      "wild_reproduction", "#1e8449",
      "stocked", "#2c6fbf",
      "designated", "#27ae60",
      "#8a9bb0",
    ],
  ]);
  map.setPaintProperty("clickable-streams-visible", "line-width", [
    "case",
    ["boolean", ["feature-state", "selected"], false],
    8,
    [
      "interpolate", ["linear"], ["get", "streamorder"], 1, 4, 7, 7,
    ],
  ]);
}

// FINDING #5: Feature-state is more elegant than Leaflet's setStyle
// loop. The "find matching features across the visible viewport" is
// O(visible features) -- the same as Leaflet's eachLayer. But the
// paint update is automatic; we don't have to "re-apply highlight
// after fetch" because feature-state persists across source.setData
// (as long as promoteId is the stable identifier).

// ====================================================================
// Pattern 7: HTML markers -- L.divIcon + L.marker -> new maplibregl.Marker({element})
// ====================================================================
//
// The condition markers (shape-coded discs) and access-point markers
// (type-coded glyph discs) and saved-pin teardrops -- all are HTML.
// MapLibre supports custom elements directly.

export function addConditionMarker(
  map: MaplibreMap,
  river: River,
  onClick: (river: River) => void,
): Marker {
  const variant = ({ green: "good", yellow: "fair", red: "poor", gray: "none" } as const)[
    river.conditions.overall
  ] || "none";
  const el = document.createElement("div");
  el.className = "condition-marker-wrap";
  el.innerHTML = `<div class="marker marker--${variant}"></div>`;
  el.addEventListener("click", (e) => {
    e.stopPropagation();
    onClick(river);
  });
  return new maplibregl.Marker({ element: el, anchor: "center" })
    .setLngLat([river.lon, river.lat]) // [lng, lat]!
    .addTo(map);
}

// FINDING #6: HTML markers in MapLibre are POSITIONED via CSS
// transforms outside the WebGL canvas, which means they don't
// benefit from GPU rendering. For ~50-100 markers this is fine
// (matches Leaflet's perf). At 1000+ markers MapLibre's GL symbol
// layers are dramatically faster but require sprite atlas setup.
// For B2a we use HTML markers -- our current marker counts are
// well under the perf cliff.

// ====================================================================
// Pattern 8: Popups -- bindPopup(html) -> new maplibregl.Popup()
// ====================================================================
//
// Direct port. Popup options match Leaflet's roughly 1:1.

export function showRiverPopup(map: MaplibreMap, river: River): Popup {
  return new maplibregl.Popup({
    maxWidth: `${Math.min(420, (window.innerWidth || 420) - 32)}px`,
    closeButton: true,
    closeOnClick: true,
  })
    .setLngLat([river.lon, river.lat])
    .setHTML(river.popup_html || "")
    .addTo(map);
}

// FINDING #7: maplibregl.Popup fires "open" event on .addTo(map)
// which we listen to in order to hydrate Lucide icons. Pattern:
//   popup.on("open", () => refreshIcons());
// Same as Leaflet's popupopen.

// ====================================================================
// Pattern 9: Tooltips -- bindTooltip(text, {sticky}) -> custom hover popup
// ====================================================================
//
// No native equivalent. Implementation: track mousemove on the
// layer, position a single shared popup at the cursor's lngLat.
// Hide on mouseleave.

export function createTooltipHelper(map: MaplibreMap): {
  bindTooltip: (layerId: string, getText: (props: any) => string | null) => void;
} {
  const tooltip = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: "ml-tooltip",
  });

  return {
    bindTooltip(layerId: string, getText: (props: any) => string | null): void {
      map.on("mousemove", layerId, (e: MapMouseEvent & { features?: maplibregl.MapGeoJSONFeature[] }) => {
        if (!e.features || !e.features.length) return;
        const text = getText(e.features[0].properties);
        if (!text) {
          tooltip.remove();
          return;
        }
        map.getCanvas().style.cursor = "pointer";
        tooltip.setLngLat(e.lngLat).setHTML(text).addTo(map);
      });
      map.on("mouseleave", layerId, () => {
        map.getCanvas().style.cursor = "";
        tooltip.remove();
      });
    },
  };
}

// FINDING #8: ~30 lines of code to replace bindTooltip. Throttled by
// mousemove naturally (browser-limited to ~60fps). Works on touch
// devices via "touchmove" too (need to add that listener -- ~5 more
// lines).

// ====================================================================
// Pattern 10: Layer visibility -- map.addLayer/removeLayer -> setLayoutProperty
// ====================================================================
//
// Layers are declared once + toggled via the "visibility" layout
// property. The existing wireLayerToggle in controls.ts:
//
//   if (cb.checked) { map.addLayer(layer); onAdd?.(); }
//   else { map.removeLayer(layer); }
//
// Becomes:
//
//   map.setLayoutProperty(layerId, "visibility", cb.checked ? "visible" : "none");
//   if (cb.checked) onAdd?.();
//
// FINDING #9: Trivial migration. Layer-state machine becomes simpler
// because layers always exist; only their visibility toggles.

// ====================================================================
// Pattern 11: Viewport queries -- getBounds, getZoom, getCenter,
//   contains, fitBounds, setView
// ====================================================================
//
// All have direct MapLibre equivalents:
//   getBounds()      -> LngLatBounds (same shape, lng/lat-ordered)
//   getZoom()        -> number
//   getCenter()      -> LngLat
//   bounds.contains  -> bounds.contains(LngLat)
//   fitBounds        -> map.fitBounds(bbox)
//   setView(c, z)    -> map.jumpTo({center: [lng, lat], zoom: z})
//                       or map.flyTo({...}) for animation
//
// The PATTERN we use most often: rivers.ts's b.contains([r.lat, r.lon])
// becomes b.contains([r.lon, r.lat]).

// ====================================================================
// Pattern 12: moveend listener -- map.on('moveend', cb)
// ====================================================================
//
// Identical API. No change needed.

// ====================================================================
// Aside on bundle size
// ====================================================================
//
// Leaflet bundles at ~140KB raw, ~44KB gzipped.
// MapLibre 5.24.0 bundles at ~1.1MB raw, ~285KB gzipped (per its
// own published metrics). Net delta: roughly +240KB gzipped.
//
// FINDING #10: This is a real mobile cost (~3x current bundle).
// Mitigations:
//   - MapLibre's code splits well; lazy-load it from a dynamic import
//     so the initial paint isn't blocked. The map shell can render
//     skeleton state during the load.
//   - Long-term (Phase 3): vector tiles would replace several
//     GeoJSON fetches, saving comparable wire bytes on the data
//     side. Net wire bytes break even within a typical session.

// ====================================================================
// Pattern 13: Service worker -- no change
// ====================================================================
//
// SW caches the Vite-hashed bundle. MapLibre's tile fetches go to
// origin-different URLs (basemaps.cartocdn.com, etc.) so the SW
// passthrough handler covers them. No SW changes needed for B2.

// ====================================================================
// Phasing recommendation -- WRITE-UP
// ====================================================================
//
// After implementing each pattern as TS reference code, the size of
// each B2 sub-PR comes into focus. RECOMMENDED PHASING:
//
//   B2a  map-setup.ts + the popup/tooltip/layer-vis helpers. Cuts
//        the cord on L.map but RETAINS Leaflet so layers can still
//        render via Leaflet adapters as a brief transition state.
//        Actually -- on reflection, parallel renderers don't work
//        cleanly. Better: B2a swaps map-setup + base tiles + ONE
//        easy overlay (river-lines as a single GeoJSON source) to
//        validate the whole pipeline end-to-end without committing
//        to swapping every layer at once.
//
//   B2b  rivers.ts: condition markers (HTML markers) + the
//        Leaflet-side renderRivers replaced by MapLibre patterns.
//        Lift makeConditionIcon. Wire feature-state highlight for
//        river-lines. River panel highlight (river-panel.ts's
//        highlightRiver) ports too -- it uses setStyle today.
//
//   B2c  streams.ts: clickable streams (the highest-value, highest-
//        complexity layer). Two-layer visible+hit pattern. Color-mode
//        toggle as setPaintProperty. Highlight state machine via
//        feature-state. This is the biggest single PR after B2a.
//
//   B2d  map-layers.ts: trout, access, public-lands overlays. Each is
//        a simple GeoJSON source + line/fill layer. Per-tier styling
//        via match expression. Click popups (public-lands).
//
//   B2e  pins.ts: saved pins (HTML markers, copper teardrop). Catch-up
//        cleanup: remove leaflet from package.json + types.d.ts,
//        rip leaflet-augment.d.ts, drop the `import * as L from
//        "leaflet"` in every module. Bundle size finally drops
//        (Leaflet exit) by ~40KB gzipped, partially offsetting
//        MapLibre's bundle.
//
// Five sub-PRs, each scoped to one renderer concern with a clear
// validation point. Same shape as B1's phasing.
//
// CRITICAL FOOTGUN to repeat in each PR description:
//   MapLibre coordinate order is [LNG, LAT].
//   Leaflet coordinate order is [LAT, LNG].
//   Every L.marker([r.lat, r.lon]) becomes
//     new maplibregl.Marker(...).setLngLat([r.lon, r.lat])
//
// Type alias to enforce at compile time:
//   type LngLat = [number, number]; // [lng, lat]
//   type LatLng = [number, number]; // [lat, lng]
//   ...and convert at boundaries.
//
// Or just be disciplined. Eight years of GIS instinct says you'll
// miss one. Build a `riverLngLat(r: River): [number, number]`
// helper and use it everywhere; one place to grep.
