/**
 * Pure state helpers + the app-wide STATES catalog + the active
 * state code. Originally extracted in PR B1b as a "pure helpers"
 * module; PR B1i expanded it to own the catalog + currentSt
 * properly (they were previously bridged from app.js).
 *
 * Owns:
 *   - STATE_ZOOM constant
 *   - deviceToken() + DEVICE_HEADER (localStorage-backed)
 *   - the STATES catalog (mutated in place via setStates() so
 *     consumers that hold a reference see updates)
 *   - the active currentSt code + setter that also updates URL
 *   - currentState() (legacy helper: resolves URL against catalog)
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

// -- States catalog ---------------------------------------------------
// Mutated in place via setStates() so any code holding a reference
// (window.STATES or the export below) sees population without having
// to re-fetch. The setter does NOT replace the object reference.

const _states: Record<string, ApiState> = {};

export function getStates(): Record<string, ApiState> {
  return _states;
}

/** Populate the catalog from the /api/states response. Mutates the
 *  shared catalog in place (does NOT reassign) so existing
 *  references stay valid. */
export function setStates(list: ApiState[]): void {
  for (const key of Object.keys(_states)) delete _states[key];
  for (const s of list) _states[s.code] = s;
}

// -- Active state code ------------------------------------------------

/** Parses ?state= from the URL on module-init. Defaults to "MD". */
function _parseInitialCurrentSt(): string {
  const p = new URLSearchParams(location.search).get("state");
  return p ? p.toUpperCase() : "MD";
}

let _currentSt = _parseInitialCurrentSt();

export function getCurrentSt(): string {
  return _currentSt;
}

interface SetCurrentStOpts {
  /** Update the URL via history.replaceState. Default true. */
  syncUrl?: boolean;
}

/**
 * Update the active state code. By default also rewrites the URL via
 * history.replaceState (no navigation) so a refresh stays on the
 * same state. Init() passes `{syncUrl: false}` because it's already
 * reading the code from the URL -- no point writing it back.
 */
export function setCurrentSt(s: string, opts?: SetCurrentStOpts): void {
  _currentSt = s;
  window.currentSt = s;
  if (opts?.syncUrl !== false) {
    history.replaceState(null, "", `/map?state=${s.toLowerCase()}`);
  }
}

/**
 * Legacy helper: resolves the URL's ?state= against the loaded
 * catalog (falling back to MD then the first known code). Used by
 * init() to pick the initial state code before calling setCurrentSt.
 */
export function currentState(): string {
  const p = new URLSearchParams(location.search).get("state") || "MD";
  const s = p.toUpperCase();
  return _states[s] ? s : (_states.MD ? "MD" : Object.keys(_states)[0]);
}

// -- Window bridge for legacy app.js ---------------------------------
// app.js's remaining code (init, etc.) reaches these via window. PR
// B1j extracts the auth + catches code that's the last consumer set;
// after that the window indirection drops here too.

declare global {
  interface Window {
    STATES: Record<string, ApiState>;
    currentSt: string;
    STATE_ZOOM: number;
    DEVICE_HEADER: Record<string, string>;
    deviceToken: () => string;
    currentState: () => string;
    getCurrentSt: () => string;
    setCurrentSt: (s: string, opts?: SetCurrentStOpts) => void;
    getStates: () => Record<string, ApiState>;
    setStates: (list: ApiState[]) => void;
  }
}

window.STATES = _states;
window.currentSt = _currentSt;
window.STATE_ZOOM = STATE_ZOOM;
window.DEVICE_HEADER = DEVICE_HEADER;
window.deviceToken = deviceToken;
window.currentState = currentState;
window.getCurrentSt = getCurrentSt;
window.setCurrentSt = setCurrentSt;
window.getStates = getStates;
window.setStates = setStates;
