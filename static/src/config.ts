/**
 * Build-time configuration flags (Vite injects `import.meta.env.VITE_*`).
 *
 * VITE_STREAM_TILES_URL — when set to the public URL of the clickable-stream
 * PMTiles archive on R2 (e.g. https://data.blueliner.app/v1/streams.pmtiles),
 * the clickable-stream network renders from vector tiles instead of the
 * per-viewport `/api/clickable_streams` GeoJSON path (MVT spike, Path A).
 *
 * Default (unset) keeps the existing GeoJSON behaviour, so this can ship
 * before the tiles exist; flip it on by setting the env at build time once
 * the archive is live on R2. See scripts/build_stream_tiles.sh.
 */

// import.meta.env isn't typed (tsconfig `types: []`), so read it defensively.
const _env = (import.meta as unknown as { env?: Record<string, string | undefined> }).env || {};

export const STREAM_TILES_URL: string = (_env.VITE_STREAM_TILES_URL || "").trim();
export const STREAM_TILES_ENABLED: boolean = STREAM_TILES_URL.length > 0;

/** The MVT layer name baked by tippecanoe (must match --layer in the build). */
export const STREAM_SOURCE_LAYER = "streams";

/**
 * VITE_PUBLIC_LANDS_TILES_URL — when set to the public R2 URL of the
 * public-lands PMTiles archive, the PAD-US parcels render from vector tiles
 * instead of the per-viewport `/api/public_lands` GeoJSON path (MVT M2).
 * Default (unset) keeps the GeoJSON behaviour. See
 * scripts/build_public_lands_tiles.sh.
 */
export const PUBLIC_LANDS_TILES_URL: string = (_env.VITE_PUBLIC_LANDS_TILES_URL || "").trim();
export const PUBLIC_LANDS_TILES_ENABLED: boolean = PUBLIC_LANDS_TILES_URL.length > 0;
export const PUBLIC_LANDS_SOURCE_LAYER = "public_lands";
