/**
 * PMTiles protocol registration (MVT spike, Path A). Both the streams and
 * public-lands vector sources read from `pmtiles://` URLs; the protocol must
 * be registered with MapLibre exactly once, so this idempotent helper is the
 * single place that does it.
 */

import maplibregl from "maplibre-gl";
import { Protocol } from "pmtiles";

let _registered = false;

export function ensurePmtilesProtocol(): void {
  if (_registered) return;
  maplibregl.addProtocol("pmtiles", new Protocol().tile);
  _registered = true;
}
