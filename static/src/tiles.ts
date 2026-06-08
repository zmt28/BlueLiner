/**
 * PMTiles protocol registration. The streams, public-lands, and vector-basemap
 * sources all read from `pmtiles://` URLs; the protocol must be registered with
 * MapLibre exactly once, so this idempotent helper is the single place that does
 * it.
 *
 * M0 (offline smoke test): we keep a reference to the Protocol instance and, for
 * the basemap archive, register a PMTiles instance backed by an IndexedDB
 * byte-range cache (offline-tiles.ts). MapLibre resolves `pmtiles://<url>` to a
 * matching added instance, so the vector base reads through the cache -- which
 * lets it render offline once a region has been prefetched. Streams/public-lands
 * stay on the default fetch source for now (wired in a later milestone).
 */

import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";
import { BASEMAP_TILES_URL } from "./config";
import { cachingPmtiles } from "./offline-tiles";

let _registered = false;
const protocol = new Protocol();

export function ensurePmtilesProtocol(): void {
  if (_registered) return;
  maplibregl.addProtocol("pmtiles", protocol.tile);
  if (BASEMAP_TILES_URL) {
    try {
      // Route the basemap archive through the offline range cache. Matched by
      // URL, so the style's `pmtiles://<BASEMAP_TILES_URL>` source uses it.
      protocol.add(cachingPmtiles(BASEMAP_TILES_URL));
    } catch (_) {
      /* offline cache unavailable -> base still renders online via fetch */
    }
  }
  _registered = true;
}
