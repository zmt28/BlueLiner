/**
 * Small dependency-free utilities used across the app (extracted from
 * the legacy app.js, PR B1c) plus the bundled Lucide icon hydrator.
 *
 * Mirrors the state.ts bridge pattern: each export is also written
 * to `window` so legacy callers can `const x = window.x`-rebind.
 * Modules that consume these import directly via ES syntax.
 */

import {
  createIcons,
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  Compass,
  Droplets,
  Filter,
  Fish,
  Layers,
  Leaf,
  LocateFixed,
  LogIn,
  LogOut,
  MailCheck,
  MapPin,
  Minus,
  Plus,
  Route,
  Search,
  Settings,
  Sparkles,
  Sprout,
  Trees,
  Waves,
  WifiOff,
  X,
} from "lucide";

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

// Every data-lucide name used anywhere in the app: static index.html,
// dynamic HTML in the TS modules, AND the server-rendered popup_html
// (main.py emits `arrow-up-right`). Icons are Vite-bundled at build
// time (M1.5) — the old unpkg.com/lucide@latest runtime script is gone.
// Adding a new data-lucide name REQUIRES adding its PascalCase icon
// here, or it silently renders as an empty <i>.
const APP_ICONS = {
  ArrowUpRight,
  Bookmark,
  Check,
  ChevronDown,
  ChevronRight,
  Compass,
  Droplets,
  Filter,
  Fish,
  Layers,
  Leaf,
  LocateFixed,
  LogIn,
  LogOut,
  MailCheck,
  MapPin,
  Minus,
  Plus,
  Route,
  Search,
  Settings,
  Sparkles,
  Sprout,
  Trees,
  Waves,
  WifiOff,
  X,
};

/**
 * Hydrate any freshly-injected `<i data-lucide="...">` nodes to inline
 * SVG. Called after every dynamic HTML render so the server-rendered
 * river panel + Python-generated popup HTML show real icons instead
 * of empty `<i>` shells.
 *
 * @param _root Kept for future scoped hydration; createIcons scans the
 *              whole document.
 */
export function refreshIcons(_root?: Element | null): void {
  try {
    createIcons({ icons: APP_ICONS });
  } catch (_) {
    /* ignore */
  }
}

// -- Window bridge ----------------------------------------------------

declare global {
  interface Window {
    esc: typeof esc;
    refreshIcons: typeof refreshIcons;
  }
}

window.esc = esc;
window.refreshIcons = refreshIcons;
