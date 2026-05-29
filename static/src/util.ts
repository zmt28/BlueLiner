/**
 * Small dependency-free utilities used across the app (extracted from
 * the legacy app.js, PR B1c). All three exports are pure functions
 * (or near-pure -- popupOpts reads window dimensions, refreshIcons
 * touches DOM but is idempotent + defensive).
 *
 * Mirrors the state.ts bridge pattern: each export is also written
 * to `window` so the still-monolithic app.js can `const x =
 * window.x`-rebind instead of keeping its own duplicate. Future
 * modules that consume these import directly via ES syntax (no
 * window indirection).
 */

/**
 * HTML-escape a value for safe interpolation into innerHTML / template
 * strings. Returns the empty string for null/undefined so callers
 * don't have to nullcheck.
 */
export function esc(s: unknown): string {
  return String(s == null ? "" : s).replace(
    /[&<>"']/g,
    (c) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[c] || c,
  );
}

/**
 * Hydrate any freshly-injected `<i data-lucide="...">` nodes to inline
 * SVG. Called after every dynamic HTML render so the server-rendered
 * river panel + Python-generated popup HTML show real icons instead
 * of empty `<i>` shells. Defensive: silently no-ops when Lucide
 * hasn't loaded yet (CDN script is deferred; an early panel open
 * before the script runs would otherwise throw).
 *
 * @param root Optional subtree to limit the scan to. Currently
 *             unused by callers (Lucide's createIcons doesn't accept
 *             a root in v0.x); the parameter is kept for future
 *             callsites that may want scoped hydration.
 */
export function refreshIcons(root?: Element | null): void {
  if (!window.lucide || !window.lucide.createIcons) return;
  try {
    window.lucide.createIcons(root ? { nameAttr: "data-lucide" } : undefined);
  } catch (_) {
    /* ignore */
  }
}

// -- Window bridge for legacy app.js ---------------------------------
// Same pattern as state.ts: app.js loses its duplicate definitions
// and rebinds `const esc = window.esc;` etc. Future TS modules
// import these via ES syntax and don't go through window.

declare global {
  interface Window {
    esc: typeof esc;
    refreshIcons: typeof refreshIcons;
  }
}

window.esc = esc;
window.refreshIcons = refreshIcons;
