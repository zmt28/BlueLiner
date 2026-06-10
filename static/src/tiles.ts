/**
 * PMTiles protocol registration. The streams, public-lands, and vector-basemap
 * sources all read from `pmtiles://` URLs; the protocol must be registered with
 * MapLibre exactly once, so this idempotent helper is the single place that does
 * it.
 *
 * Offline (Phase 2): we keep a reference to the Protocol instance and register
 * the basemap AND clickable-streams archives as PMTiles instances backed by an
 * IndexedDB byte-range cache (offline-tiles.ts). MapLibre resolves
 * `pmtiles://<url>` to a matching added instance, so those layers read through
 * the cache -- which lets them render offline once an area has been downloaded.
 * Public-lands stays on the default fetch source (rarely needed off-grid).
 */

import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { BASEMAP_TILES_URL, STREAM_TILES_URL } from "./config";
import { cachingPmtiles } from "./offline-tiles";

let _registered = false;
const protocol = new Protocol();

export function ensurePmtilesProtocol(): void {
  if (_registered) return;
  maplibregl.addProtocol("pmtiles", protocol.tile);
  // Route the basemap + streams archives through the offline range cache.
  // Matched by URL, so the `pmtiles://<url>` sources in the style/streams use
  // these instances.
  for (const url of [BASEMAP_TILES_URL, STREAM_TILES_URL]) {
    if (!url) continue;
    try {
      protocol.add(cachingPmtiles(url));
    } catch (_) {
      /* offline cache unavailable -> layer still renders online via fetch */
    }
  }
  _registered = true;
}
