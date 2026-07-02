/**
 * Shared "a mode owns map clicks" registry. When a mode that places or
 * frames something on the map is armed (drop-a-pin, offline-download
 * framing), feature click handlers (streams, POIs, public lands,
 * trails) stand down so the arming click doesn't ALSO select whatever
 * is under it — the bug the pin flow had before its overhaul.
 *
 * Dependency-free so pins.ts, controls.ts, streams.ts, and
 * map-layers.ts can all import it without cycles.
 */

const _claims = new Set<string>();

/** Arm a mode: feature click handlers stand down until released. */
export function claimMapClicks(tag: string): void {
  _claims.add(tag);
}

export function releaseMapClicks(tag: string): void {
  _claims.delete(tag);
}

/** True while any mode owns map clicks. */
export function mapClicksClaimed(): boolean {
  return _claims.size > 0;
}
