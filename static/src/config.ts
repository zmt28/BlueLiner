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

// Self-hosted vector basemap (offline-ready basemap, Phase 0). Points at
// `basemap.pmtiles` built + published by scripts/build_basemap_tiles.sh. The
// companion style/glyphs/sprite live under the same versioned prefix, next to
// the archive:
//   <prefix>/basemap.pmtiles
//   <prefix>/basemap/style.json
//   <prefix>/basemap/fonts/{fontstack}/{range}.pbf
//   <prefix>/basemap/sprites/v4/<theme>.{png,json}
// Unset (today) => no vector base is offered; the raster bases are unaffected.
// Phase 1 wires this into map-setup.ts as a 4th base option.
export const BASEMAP_TILES_URL: string = (_env.VITE_BASEMAP_TILES_URL || "").trim();
export const BASEMAP_TILES_ENABLED: boolean = BASEMAP_TILES_URL.length > 0;
/** Style.json sibling of the basemap archive (same versioned prefix). */
export const BASEMAP_STYLE_URL: string = BASEMAP_TILES_ENABLED
  ? BASEMAP_TILES_URL.replace(/basemap\.pmtiles$/, "basemap/style.json")
  : "";
