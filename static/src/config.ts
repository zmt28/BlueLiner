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

/**
 * VITE_BASEMAP_TILES_URL — public R2 URL of the vector *basemap* PMTiles
 * (Protomaps basemap schema), e.g. https://data.blueliner.app/v1/basemap.pmtiles.
 * Unset = the basemap stays raster (CARTO / Esri / USGS, the default); set it
 * to light up the "Vector" base option. The same file is what a mobile build
 * bundles/downloads for offline use (read via file://), so this web wiring
 * doubles as the offline foundation. See scripts/build_basemap_tiles.sh.
 */
export const BASEMAP_TILES_URL: string = (_env.VITE_BASEMAP_TILES_URL || "").trim();
export const BASEMAP_TILES_ENABLED: boolean = BASEMAP_TILES_URL.length > 0;
