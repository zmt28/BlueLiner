/**
 * Pure state helpers extracted from the legacy app.js (PR B1b, first
 * canonical TS module).
 *
 * Scope decisions:
 *   - STATE_ZOOM is a constant; lives here.
 *   - deviceToken() / DEVICE_HEADER are pure (read localStorage,
 *     build a header object); live here.
 *   - currentState() is pure if we let it read the loaded states
 *     catalog from `window.STATES`; lives here, reads via window.
 *   - STATES (the loaded catalog) and currentSt (the active code)
 *     stay in app.js for now -- they're hot mutable state that
 *     app.js updates throughout init + the state selector handler.
 *     A future PR that fully extracts those code paths can move
 *     them here.
 *
 * Bridge: app.js assigns `window.STATES = STATES` once at top so
 * the catalog is reachable from this module via window. Removing
 * the bridge is a follow-up PR's job (when the consumers stop
 * referencing the app.js-local copy).
 */

export const STATE_ZOOM = 7;

/**
 * Opaque per-device token (no login). Persists in localStorage; sent
 * as `X-Device-Token` on every pins request so saved pins are scoped
 * to this device/browser without requiring an account.
 */
export function deviceToken(): string {
  let t = localStorage.getItem("bl_device");
  if (!t) {
    t = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : String(Date.now()) + Math.random().toString(36).slice(2);
    localStorage.setItem("bl_device", t);
  }
  return t;
}

export const DEVICE_HEADER: Record<string, string> = {
  "X-Device-Token": deviceToken(),
};

/**
 * Resolves the active state code from the URL (?state=PA) or falls
 * back to MD. Returns the first known code if MD itself isn't in
 * the loaded states catalog.
 *
 * Reads the catalog from `window.STATES` (assigned by app.js at top
 * of file as the legacy bridge). When app.js itself is fully
 * migrated, the catalog will live in this module too and the window
 * indirection drops.
 */
export function currentState(): string {
  const catalog = window.STATES || {};
  const p = new URLSearchParams(location.search).get("state") || "MD";
  const s = p.toUpperCase();
  return catalog[s] ? s : (catalog.MD ? "MD" : Object.keys(catalog)[0]);
}

// -- Window bridge for legacy app.js ---------------------------------
// app.js still defines its own local STATES / currentSt; it ALSO
// reads these globals (after this PR) for the symbols extracted
// here. The window assignments below let app.js call window.deviceToken()
// / window.currentState() / etc. instead of its own duplicates,
// which are removed in this PR's app.js edit.

declare global {
  interface Window {
    STATES: Record<string, ApiState>;
    currentSt: string;
    STATE_ZOOM: number;
    DEVICE_HEADER: Record<string, string>;
    deviceToken: () => string;
    currentState: () => string;
  }
}

window.STATE_ZOOM = STATE_ZOOM;
window.DEVICE_HEADER = DEVICE_HEADER;
window.deviceToken = deviceToken;
window.currentState = currentState;
