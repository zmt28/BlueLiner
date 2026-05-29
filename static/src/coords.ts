/**
 * Coordinate helpers — the single grep-point for the Leaflet→MapLibre
 * coordinate-order flip.
 *
 * Leaflet takes [lat, lng]. MapLibre takes [lng, lat]. Every place a
 * River, an ApiState center, or a bounds turns into a coordinate for the
 * map MUST go through one of these, so "why did it render in the wrong
 * hemisphere" has exactly one file to check.
 */

import type { LngLatLike, LngLatBounds, Map as MaplibreMap } from "maplibre-gl";

/** A River's position as MapLibre [lng, lat]. */
export function riverLngLat(r: { lat: number; lon: number }): [number, number] {
  return [r.lon, r.lat];
}

/** An ApiState.center (server-sent as [lat, lon]) as MapLibre [lng, lat]. */
export function centerLngLat(center: [number, number]): [number, number] {
  return [center[1], center[0]];
}

/** Generic [lat, lng] -> [lng, lat]. */
export function latLngToLngLat(latLng: [number, number]): [number, number] {
  return [latLng[1], latLng[0]];
}

/** The map's current viewport as a [west, south, east, north] bbox — the
 *  shape every `/api/*?bbox=w,s,e,n` fetcher expects. */
export function bboxFromBounds(map: MaplibreMap): [number, number, number, number] {
  const b: LngLatBounds = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
}

/** True if [lng, lat] is inside the map's current viewport. Replaces
 *  Leaflet's `map.getBounds().contains([lat, lng])`. */
export function boundsContainLngLat(
  map: MaplibreMap,
  lngLat: [number, number],
): boolean {
  return map.getBounds().contains(lngLat as LngLatLike);
}
