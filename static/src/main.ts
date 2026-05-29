// Blueliner frontend entry point (Vite-managed).
//
// Kept deliberately tiny: it imports the global CSS eagerly (so the
// static chrome shell in index.html is styled on first paint) and then
// lazy-loads the interactive app — MapLibre GL JS + every map module —
// as a separate chunk via dynamic import. That ~300 KB gzipped renderer
// downloads/executes AFTER first paint instead of blocking it (PR B2f).
//
// Vite code-splits the dynamic import into its own hashed chunk; the
// service worker's networkFirst rule for /static/ caches it after the
// first load, so returning/offline visits reuse it.

import "../tokens.css";
import "../app.css";
import "maplibre-gl/dist/maplibre-gl.css";

import("./app-boot");

// -- Service worker -------------------------------------------------
// Register early (independent of the map chunk) + auto-reload once when a
// new worker takes control so a deploy propagates fresh JS/CSS without a
// manual cache clear. Only armed when the page is already controlled (a
// returning visit); the very first visit has no stale assets to replace.

if ("serviceWorker" in navigator) {
  if (navigator.serviceWorker.controller) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      refreshing = true;
      window.location.reload();
    });
  }
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      /* ignore */
    });
  });
}
