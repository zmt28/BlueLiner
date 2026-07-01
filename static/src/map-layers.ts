/**
 * Overlay layers + their lazy-load fetchers + popup helpers, on MapLibre
 * GL JS (PR B2). Replaces the Leaflet layer-group version.
 *
 * Owns:
 *   - access           glyph-coded access-point HTML markers (lazy per-state),
 *                       toggled PER TYPE (boat ramp / walk-in / wading / pier /
 *                       parking) -- one bucket per type, independent visibility
 *   - stocked          stocked-water HTML markers (lazy per-state); shown
 *                       when the toggle is on OR the Stocked map style is active
 *   - public-lands     PAD-US parcels (GeoJSON source + fill/line layers,
 *                       bbox-bound viewport fetch)
 *   - visibility setters + lazy-load fns: setAccessTypeVisible / anyAccessVisible
 *     / ensureAccess, setStockedVisible / setStockedStyleActive / ensureStocked,
 *     setPublicLandsVisible
 *   - the popup-html helpers (accessPopupHtml, stockedPopupHtml,
 *     publicLandsPopupHtml) and the marker element factories
 *
 * Sources/layers are added in onMapReady (MapLibre rejects addSource/
 * addLayer before the style `load` fires). Initial visibility reflects
 * the desired-state vars below, which controls.ts sets from saved prefs
 * before `load`.
 */

import { LayerSpecification, FilterSpecification } from "maplibre-gl";
import { map, onMapReady } from "./map-setup";
import { esc } from "./util";
import { makePopup } from "./popups";
import {
  PUBLIC_LANDS_TILES_ENABLED,
  PUBLIC_LANDS_TILES_URL,
  PUBLIC_LANDS_SOURCE_LAYER,
  TRAILS_TILES_ENABLED,
  TRAILS_TILES_URL,
  TRAILS_SOURCE_LAYER,
  ACCESS_TILES_ENABLED,
  ACCESS_TILES_URL,
  ACCESS_SOURCE_LAYER,
  DAMS_TILES_ENABLED,
  DAMS_TILES_URL,
  DAMS_SOURCE_LAYER,
  STOCKING_TILES_ENABLED,
  STOCKING_TILES_URL,
  STOCKING_SOURCE_LAYER,
} from "./config";
import { ensurePmtilesProtocol } from "./tiles";
import { registerPoiIcons } from "./poi-map-icons";
import { directionsLinkHtml } from "./directions";

// Desired visibility (matches the HTML checkbox defaults; controls.ts
// overrides from saved prefs before the map `load` fires).
let _publicLandsVisible = false;
let _trailsVisible = false;

function vis(on: boolean): "visible" | "none" {
  return on ? "visible" : "none";
}

// -- Access points ------------------------------------------------------
// A vector-tile symbol layer -- the access TYPE is the glyph (sailboat /
// footprints / dock / P) via icon-image. Each type is an independent toggle
// (lyr-access-<type>); the enabled set is a layer filter. Fishing/wading spots,
// trailheads and generic walk-ins fold into one "fishing_access" type; an
// unknown type also falls into fishing_access.

const ACCESS_TYPES = [
  "boat_ramp",
  "fishing_access",
  "pier",
  "parking",
] as const;
type AccessType = (typeof ACCESS_TYPES)[number];

function _accessBucket(type: string | undefined): AccessType {
  return (ACCESS_TYPES as readonly string[]).includes(type || "")
    ? (type as AccessType)
    : "fishing_access";
}

// Per-type desired visibility (controls.ts overrides from saved prefs
// before `load`). All off by default -- access is opt-in.
const _accessTypeVisible: Record<AccessType, boolean> = {
  boat_ramp: false,
  fishing_access: false,
  pier: false,
  parking: false,
};

// Provenance label for the overlay's `source` -- tells the user where the
// coordinate came from (and, with `precision`, how trustworthy it is).
const _ACCESS_SOURCE_LABEL: Record<string, string> = {
  osm: "OpenStreetMap",
  ridb: "Recreation.gov",
  agency: "State agency",
};

export function accessPopupHtml(
  p: AccessFeatureProps,
  lngLat?: [number, number],
): string {
  const accessChip = p.access
    ? `<span class="ap-chip ap-chip-${esc(p.access)}">${esc(p.access)}</span>`
    : "";
  const typeLabel = String(p.type || "fishing_access").replace(/_/g, " ");
  const notes = p.notes ? `<div class="ap-notes">${esc(p.notes)}</div>` : "";
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">Agency info &rarr;</a></div>`
    : "";
  // Provenance: "OpenStreetMap · mapped" / "State agency · surveyed". Helps the
  // angler weigh a mapped (OSM, community) vs surveyed (agency/RIDB) coordinate.
  const srcLabel = p.source ? _ACCESS_SOURCE_LABEL[p.source] || p.source : "";
  const src = srcLabel
    ? `<div class="ap-source">${esc(srcLabel)}` +
      (p.precision ? ` &middot; ${esc(p.precision)}` : "") +
      `</div>`
    : "";
  const dir = lngLat ? directionsLinkHtml(lngLat[1], lngLat[0], p.name) : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Access point")}</div>` +
    `<div class="ap-meta">${esc(typeLabel)}${accessChip}</div>` +
    notes +
    link +
    src +
    dir +
    `</div>`
  );
}

// -- Generic point-tile layer -------------------------------------------
// access / dams / stocking are now static PMTiles on R2 (the POI tile
// pipeline), rendered as GPU symbol layers with the shared glyph icons
// (poi-map-icons) instead of per-feature DOM markers fed by /api/*. National +
// viewport-driven: no per-state fetch, no reload on state change, and the old
// zoom/count marker gates are gone (the GPU handles density). An unset
// VITE_*_TILES_URL just skips the layer, matching streams/trails/public-lands.

interface PointTileOpts {
  key: string; // "access" | "dams" | "stocking"
  url: string;
  sourceLayer: string;
  iconImage: unknown; // icon-image expression or a literal image id
  visible: boolean;
  filter?: unknown;
  popupHtml: (p: Record<string, unknown>, lngLat: [number, number]) => string;
}

// Point overlays only matter once you're reading a river; keep them off the
// low-zoom view (what the old marker zoom-gates approximated).
const POI_MIN_ZOOM = 7;

function addPointTileLayer(o: PointTileOpts): void {
  ensurePmtilesProtocol();
  const src = `${o.key}-src`;
  const lyr = `${o.key}-pts`;
  if (!map.getSource(src)) {
    map.addSource(src, { type: "vector", url: `pmtiles://${o.url}` });
  }
  // Icons rasterize asynchronously; add the symbol layer only once they're
  // registered so the first paint has glyphs (a missing icon renders nothing).
  void registerPoiIcons().then(() => {
    if (map.getLayer(lyr)) return;
    map.addLayer({
      id: lyr,
      type: "symbol",
      source: src,
      "source-layer": o.sourceLayer,
      minzoom: POI_MIN_ZOOM,
      ...(o.filter ? { filter: o.filter as FilterSpecification } : {}),
      layout: {
        visibility: vis(o.visible),
        "icon-image": o.iconImage,
        "icon-size": ["interpolate", ["linear"], ["zoom"], 8, 0.75, 13, 1],
        "icon-allow-overlap": true,
        "icon-ignore-placement": true,
      },
    } as LayerSpecification);
    const popup = makePopup();
    map.on("click", lyr, (e) => {
      const f = e.features && e.features[0];
      if (!f) return;
      // Selecting a POI click -> close the rail panel.
      document.dispatchEvent(new Event("bl:poi-open"));
      popup
        .setLngLat(e.lngLat)
        .setHTML(
          o.popupHtml((f.properties || {}) as Record<string, unknown>, [
            e.lngLat.lng,
            e.lngLat.lat,
          ]),
        )
        .addTo(map);
    });
    map.on("mouseenter", lyr, () => {
      map.getCanvas().style.cursor = "pointer";
    });
    map.on("mouseleave", lyr, () => {
      map.getCanvas().style.cursor = "";
    });
  });
}

// Per-type access visibility is a layer filter over the one symbol layer:
// only the enabled types render, and the layer hides entirely when none are.
function _accessFilter(): FilterSpecification {
  const on = ACCESS_TYPES.filter((t) => _accessTypeVisible[t]);
  return ["in", ["get", "type"], ["literal", on]] as unknown as FilterSpecification;
}

function _applyAccessVisibility(): void {
  if (!map.getLayer("access-pts")) return;
  map.setLayoutProperty("access-pts", "visibility", vis(anyAccessVisible()));
  map.setFilter("access-pts", _accessFilter());
}

/** True if any access type is toggled on. */
export function anyAccessVisible(): boolean {
  return ACCESS_TYPES.some((t) => _accessTypeVisible[t]);
}

/** Wired to each per-type lyr-access-<type> checkbox. */
export function setAccessTypeVisible(type: string, on: boolean): void {
  _accessTypeVisible[_accessBucket(type)] = on;
  _applyAccessVisibility();
}

onMapReady(() => {
  if (!ACCESS_TILES_ENABLED) return;
  addPointTileLayer({
    key: "access",
    url: ACCESS_TILES_URL,
    sourceLayer: ACCESS_SOURCE_LAYER,
    iconImage: ["concat", "poi-", ["get", "type"]],
    filter: _accessFilter(),
    visible: anyAccessVisible(),
    popupHtml: (p, ll) => accessPopupHtml(p as AccessFeatureProps, ll),
  });
});

// -- Stocked waters -----------------------------------------------------
// Curated + live stocking points (one pin each). Shown when EITHER the
// "Stocked waters" toggle is on OR the Stocked map style is active -- so the
// style surfaces real stocking locations on top of the stocked-stream color.

let _stockedToggle = false; // the lyr-stocked checkbox
let _stockedStyleActive = false; // the Stocked map style

export function stockedPopupHtml(
  p: StockedFeatureProps,
  lngLat?: [number, number],
): string {
  const species = (p.species || []).join(", ");
  const meta = [species, p.category ? esc(p.category) : "",
                p.season ? esc(p.season) : ""].filter(Boolean).join(" &middot; ");
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">Stocking schedule &rarr;</a></div>`
    : "";
  const dir = lngLat ? directionsLinkHtml(lngLat[1], lngLat[0], p.water) : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.water || "Stocked water")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    link +
    dir +
    `</div>`
  );
}

function _stockedShouldShow(): boolean {
  return _stockedToggle || _stockedStyleActive;
}

function _applyStockedVisibility(): void {
  if (map.getLayer("stocking-pts")) {
    map.setLayoutProperty("stocking-pts", "visibility", vis(_stockedShouldShow()));
  }
}

/** Wired to the lyr-stocked checkbox (the wireLayerToggle setter). */
export function setStockedVisible(on: boolean): void {
  _stockedToggle = on;
  _applyStockedVisibility();
}

/** Force-shows the stocked-waters layer regardless of the toggle. (Previously
 *  driven by the retired "Stocked" map-style; retained for callers that want to
 *  force the layer on.) */
export function setStockedStyleActive(on: boolean): void {
  _stockedStyleActive = on;
  _applyStockedVisibility();
}

/** Retained for the state-selector's call site. The layer is national tiles
 *  (viewport-driven), so there's nothing to reload -- just re-apply visibility. */
export function refreshStockedForState(_state: string): void {
  _applyStockedVisibility();
}

// Vector-tile properties can't hold arrays, so tippecanoe serializes the
// stocking `species` list to a JSON string; parse it back before the popup
// joins it. Everything else on the tile is already scalar.
function _stockedTilePopup(
  p: Record<string, unknown>,
  lngLat: [number, number],
): string {
  let species: unknown = p.species;
  if (typeof species === "string") {
    try {
      species = JSON.parse(species);
    } catch {
      species = species ? [species] : [];
    }
  }
  return stockedPopupHtml({ ...p, species } as StockedFeatureProps, lngLat);
}

onMapReady(() => {
  if (!STOCKING_TILES_ENABLED) return;
  addPointTileLayer({
    key: "stocking",
    url: STOCKING_TILES_URL,
    sourceLayer: STOCKING_SOURCE_LAYER,
    iconImage: "poi-stocked",
    visible: _stockedShouldShow(),
    popupHtml: _stockedTilePopup,
  });
});

// -- Dams (NID) ---------------------------------------------------------
// USACE National Inventory of Dams, one brand-blue disc with the dam glyph.
// National static PMTiles (built from the NID national layer); no per-state
// fetch or zoom/count gate -- the GPU symbol layer handles the ~92k points.

let _damsVisible = false;

export function damPopupHtml(
  p: DamFeatureProps,
  lngLat?: [number, number],
): string {
  const meta = [
    p.river ? `on ${esc(p.river)}` : "",
    p.owner ? esc(p.owner) : "",
    p.year ? esc(p.year) : "",
    p.height_ft ? `${p.height_ft} ft` : "",
  ]
    .filter(Boolean)
    .join(" &middot; ");
  const purposes = p.purposes
    ? `<div class="ap-notes">${esc(p.purposes)}</div>`
    : "";
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" ` +
      `rel="noopener noreferrer">NID record &rarr;</a></div>`
    : "";
  const dir = lngLat ? directionsLinkHtml(lngLat[1], lngLat[0], p.name) : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Dam")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    purposes +
    link +
    dir +
    `</div>`
  );
}

function _applyDamsVisibility(): void {
  if (map.getLayer("dams-pts")) {
    map.setLayoutProperty("dams-pts", "visibility", vis(_damsVisible));
  }
}

export function setDamsVisible(on: boolean): void {
  _damsVisible = on;
  _applyDamsVisibility();
}

onMapReady(() => {
  if (!DAMS_TILES_ENABLED) return;
  addPointTileLayer({
    key: "dams",
    url: DAMS_TILES_URL,
    sourceLayer: DAMS_SOURCE_LAYER,
    iconImage: "poi-dam",
    visible: _damsVisible,
    popupHtml: (p, ll) => damPopupHtml(p as DamFeatureProps, ll),
  });
});

// -- Public lands (PAD-US) ----------------------------------------------
// Tier-keyed styling via `match` expressions on the public_access prop:
// OA = open access (green), RA = restricted (dashed yellow), XA/UK hidden.

const PA_ACCESS_LABEL: Record<PublicAccessTier, string> = {
  OA: "Open access",
  RA: "Restricted access",
  XA: "Closed",
  UK: "Unknown",
};

export function publicLandsPopupHtml(
  p: PublicLandsProps,
  lngLat?: [number, number],
): string {
  const tierCode = (p.public_access as PublicAccessTier) || "UK";
  const tierLabel = PA_ACCESS_LABEL[tierCode] || PA_ACCESS_LABEL.UK;
  const tierChip = `<span class="ap-chip pa-chip-${esc(tierCode)}">${esc(tierLabel)}</span>`;
  const lines = [
    `<div class="ap-name">${esc(p.unit_name || "Public land parcel")}</div>`,
  ];
  const sub: string[] = [];
  const manager = (p as { manager_name?: string }).manager_name;
  if (manager) sub.push(esc(manager));
  if (p.designation) sub.push(esc(p.designation));
  if (sub.length) lines.push(`<div class="ap-meta">${sub.join(" &middot; ")}</div>`);
  lines.push(`<div class="ap-meta" style="margin-top:6px">${tierChip}</div>`);
  const stateNm = (p as { state_nm?: string }).state_nm;
  if (stateNm) lines.push(`<div class="ap-notes">${esc(stateNm)}</div>`);
  if (lngLat) lines.push(directionsLinkHtml(lngLat[1], lngLat[0], p.unit_name));
  return `<div class="ap-popup">${lines.join("")}</div>`;
}

const PL_SRC_LAYER = { "source-layer": PUBLIC_LANDS_SOURCE_LAYER };

onMapReady(() => {
  // GeoJSON fallback retired in M3 — public lands is served only as static
  // PMTiles on R2 (read via pmtiles://, configured by
  // VITE_PUBLIC_LANDS_TILES_URL). MapLibre fetches/decodes the tiles itself.
  if (!PUBLIC_LANDS_TILES_ENABLED) return;
  ensurePmtilesProtocol();
  map.addSource("public-lands", {
    type: "vector",
    url: `pmtiles://${PUBLIC_LANDS_TILES_URL}`,
  });
  map.addLayer({
    id: "public-lands-fill",
    type: "fill",
    source: "public-lands",
    ...PL_SRC_LAYER,
    layout: { visibility: vis(_publicLandsVisible) },
    paint: {
      "fill-color": [
        "match",
        ["get", "public_access"],
        "OA", "#2d6a4f",
        "RA", "#eab308",
        "#000",
      ],
      "fill-opacity": [
        "match",
        ["get", "public_access"],
        "OA", 0.28,
        "RA", 0.22,
        0,
      ],
    },
  } as LayerSpecification);
  map.addLayer({
    id: "public-lands-line",
    type: "line",
    source: "public-lands",
    ...PL_SRC_LAYER,
    layout: { visibility: vis(_publicLandsVisible) },
    paint: {
      "line-color": [
        "match",
        ["get", "public_access"],
        "OA", "#1b4332",
        "RA", "#854d0e",
        "#000",
      ],
      "line-width": ["match", ["get", "public_access"], "OA", 0.8, "RA", 1.0, 0],
      "line-opacity": ["match", ["get", "public_access"], "OA", 1, "RA", 1, 0],
      "line-dasharray": ["match", ["get", "public_access"], "RA", ["literal", [4, 4]], ["literal", [1, 0]]],
    },
  } as LayerSpecification);
  const popup = makePopup();
  map.on("click", "public-lands-fill", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    popup
      .setLngLat(e.lngLat)
      .setHTML(
        publicLandsPopupHtml((f.properties || {}) as PublicLandsProps, [
          e.lngLat.lng,
          e.lngLat.lat,
        ]),
      )
      .addTo(map);
  });
});

export function setPublicLandsVisible(on: boolean): void {
  _publicLandsVisible = on;
  for (const id of ["public-lands-fill", "public-lands-line"]) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", vis(on));
  }
}

// -- River trails (USGS National Digital Trails) -------------------------
// A static PMTiles LINE layer (built-time filtered to trails running
// alongside the stream network), same pattern as public-lands. Dashed
// amber so it reads as a footpath distinct from the tier-colored streams.

const TRAILS_SRC_LAYER = { "source-layer": TRAILS_SOURCE_LAYER };

export function trailPopupHtml(
  p: TrailProps,
  lngLat?: [number, number],
): string {
  const meta = [
    p.trail_type ? esc(p.trail_type) : "",
    p.surface ? esc(p.surface) : "",
    p.length_mi ? `${p.length_mi} mi` : "",
  ]
    .filter(Boolean)
    .join(" &middot; ");
  const dir = lngLat ? directionsLinkHtml(lngLat[1], lngLat[0], p.name) : "";
  return (
    `<div class="ap-popup">` +
    `<div class="ap-name">${esc(p.name || "Trail")}</div>` +
    (meta ? `<div class="ap-meta">${meta}</div>` : "") +
    dir +
    `</div>`
  );
}

onMapReady(() => {
  if (!TRAILS_TILES_ENABLED) return;
  ensurePmtilesProtocol();
  map.addSource("trails", {
    type: "vector",
    url: `pmtiles://${TRAILS_TILES_URL}`,
  });
  map.addLayer({
    id: "trails-line",
    type: "line",
    source: "trails",
    ...TRAILS_SRC_LAYER,
    layout: {
      visibility: vis(_trailsVisible),
      "line-cap": "round",
      "line-join": "round",
    },
    paint: {
      "line-color": "#b45309",
      "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.8, 14, 2.4],
      "line-opacity": 0.85,
      "line-dasharray": [2, 1.5],
    },
  } as LayerSpecification);
  const popup = makePopup();
  map.on("click", "trails-line", (e) => {
    const f = e.features && e.features[0];
    if (!f) return;
    popup
      .setLngLat(e.lngLat)
      .setHTML(
        trailPopupHtml((f.properties || {}) as TrailProps, [
          e.lngLat.lng,
          e.lngLat.lat,
        ]),
      )
      .addTo(map);
  });
});

export function setTrailsVisible(on: boolean): void {
  _trailsVisible = on;
  if (map.getLayer("trails-line")) {
    map.setLayoutProperty("trails-line", "visibility", vis(on));
  }
}
