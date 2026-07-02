/**
 * Build-time configuration (Vite injects `import.meta.env.VITE_*`).
 *
 * VITE_STREAM_TILES_URL / VITE_PUBLIC_LANDS_TILES_URL — public R2 URLs of the
 * clickable-stream and public-lands PMTiles archives (e.g.
 * https://data.blueliner.app/v1/streams.pmtiles). Since M3 retired the
 * per-viewport GeoJSON paths, these are the only source of those layers: an
 * unset URL means the layer simply isn't added to the map (no fallback). Set
 * them at build time (Render build env / docker build-arg). See
 * scripts/build_stream_tiles.sh and scripts/build_public_lands_tiles.sh.
 */

// import.meta.env isn't typed (tsconfig `types: []`), so read it defensively.
const _env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env || {};

/**
 * Runtime data-release token, injected into the shell `<head>` by the server
 * (`<meta name="bl-data-version" content="v4">`, see main.py). The R2-backed
 * overlay endpoints (/api/access, /api/stocking) carry it as `?v=<token>` so a
 * data-only refresh published under a new R2 prefix busts the URL-keyed CDN
 * cache without a manual Cloudflare purge. Falls back to "local" in dev (Vite
 * serves the raw shell, so the meta tag isn't present).
 */
export const DATA_VERSION: string =
  (typeof document !== "undefined"
    ? document
        .querySelector('meta[name="bl-data-version"]')
        ?.getAttribute("content")
    : null
  )?.trim() || "local";

export const STREAM_TILES_URL: string = (_env.VITE_STREAM_TILES_URL || "").trim();
export const STREAM_TILES_ENABLED: boolean = STREAM_TILES_URL.length > 0;

/** The MVT layer name baked by tippecanoe (must match --layer in the build). */
export const STREAM_SOURCE_LAYER = "streams";

export const PUBLIC_LANDS_TILES_URL: string = (_env.VITE_PUBLIC_LANDS_TILES_URL || "").trim();
export const PUBLIC_LANDS_TILES_ENABLED: boolean = PUBLIC_LANDS_TILES_URL.length > 0;
export const PUBLIC_LANDS_SOURCE_LAYER = "public_lands";

// River trails (USGS National Map National Digital Trails, filtered at build
// time to segments running alongside the stream network). Same static-PMTiles
// pattern as streams/public-lands: unset URL => the layer isn't added. Built
// by scripts/build_trails.py + scripts/build_trail_tiles.sh.
export const TRAILS_TILES_URL: string = (_env.VITE_TRAILS_TILES_URL || "").trim();
export const TRAILS_TILES_ENABLED: boolean = TRAILS_TILES_URL.length > 0;
/** MVT layer name baked by tippecanoe (must match --layer in the build). */
export const TRAILS_SOURCE_LAYER = "trails";

// Point overlays (access / dams / stocking) as static PMTiles — the same
// serverless pattern as streams/trails, replacing the in-RAM /api/{access,
// dams,stocking} GeoJSON endpoints that OOMed the free instance. Built by the
// GeoJSON producers (build_river_poi.py / build_overlay_geojson.py) +
// scripts/build_poi_tiles.sh. Unset URL => that layer isn't added (the app
// degrades to no overlay, never to a dynamic fetch). The MVT layer name MUST
// match the tippecanoe --layer (== the key here).
export const ACCESS_TILES_URL: string = (_env.VITE_ACCESS_TILES_URL || "").trim();
export const ACCESS_TILES_ENABLED: boolean = ACCESS_TILES_URL.length > 0;
export const ACCESS_SOURCE_LAYER = "access";

export const DAMS_TILES_URL: string = (_env.VITE_DAMS_TILES_URL || "").trim();
export const DAMS_TILES_ENABLED: boolean = DAMS_TILES_URL.length > 0;
export const DAMS_SOURCE_LAYER = "dams";

export const STOCKING_TILES_URL: string = (_env.VITE_STOCKING_TILES_URL || "").trim();
export const STOCKING_TILES_ENABLED: boolean = STOCKING_TILES_URL.length > 0;
export const STOCKING_SOURCE_LAYER = "stocking";

export const FLYSHOPS_TILES_URL: string = (_env.VITE_FLYSHOPS_TILES_URL || "").trim();
export const FLYSHOPS_TILES_ENABLED: boolean = FLYSHOPS_TILES_URL.length > 0;
export const FLYSHOPS_SOURCE_LAYER = "flyshops";

// Client-side search index (M4.2): gauges + counties + towns, built by
// scripts/build_search_index.py and fetched lazily on first search
// focus. Point at the .json.gz; the client falls back to the sibling
// plain .json when DecompressionStream is unavailable. Unset => search
// covers the live river catalog only (the pre-M4.2 behavior).
export const SEARCH_INDEX_URL: string = (_env.VITE_SEARCH_INDEX_URL || "").trim();
export const SEARCH_INDEX_ENABLED: boolean = SEARCH_INDEX_URL.length > 0;

// Self-hosted vector basemap (offline-ready basemap, Phase 0). Points at
// `basemap.pmtiles` built + published by scripts/build_basemap_tiles.sh. The
// companion style/glyphs/sprite live under the same versioned prefix, next to
// the archive:
//   <prefix>/basemap.pmtiles
//   <prefix>/basemap/style.json
//   <prefix>/basemap/fonts/{fontstack}/{range}.pbf
//   <prefix>/basemap/sprites/v4/<theme>.{png,json}
// SET IN PRODUCTION since 2026-06-08 (Render dashboard build env, not
// render.yaml): https://data.blueliner.app/v5/basemap.pmtiles — the vector
// base + offline downloads are LIVE. Unset (dev/CI default) => no vector
// base is offered; the raster bases are unaffected.
export const BASEMAP_TILES_URL: string = (_env.VITE_BASEMAP_TILES_URL || "").trim();
export const BASEMAP_TILES_ENABLED: boolean = BASEMAP_TILES_URL.length > 0;
/** Style.json sibling of the basemap archive (same versioned prefix). */
export const BASEMAP_STYLE_URL: string = BASEMAP_TILES_ENABLED
  ? BASEMAP_TILES_URL.replace(/basemap\.pmtiles$/, "basemap/style.json")
  : "";
