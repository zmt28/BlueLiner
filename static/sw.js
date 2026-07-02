// @ts-check
/// <reference lib="webworker" />
// In a service worker, `self` is a ServiceWorkerGlobalScope, not a Window.
// Tell TypeScript that explicitly so .skipWaiting() / .clients are typed.
/** @typedef {ServiceWorkerGlobalScope} SWGlobalScope */
/** @type {SWGlobalScope} */
const swSelf = /** @type {any} */ (self);

// Blueliner service worker -- offline app shell only.
// Live data (/api/*) and map tiles are always fetched from the network.
//
// App code (app.js/app.css/manifest) and navigations are NETWORK-FIRST so
// deploys propagate immediately (cache-first here meant returning browsers
// ran stale JS forever). Only the immutable vendored Leaflet + icons are
// cache-first. Bump CACHE on any caching-strategy change.
// Bump this on any change to the cached shell (app.js, app.css, sw.js
// strategies). The activate handler below deletes every cache whose
// name doesn't match the current CACHE constant, which forces every
// returning browser to refetch the shell on next visit -- the only
// reliable way to roll out a buggy client-side change.
const CACHE = "blueliner-v25";
// Vite production builds emit fingerprinted asset filenames (e.g.
// `/static/dist/assets/index-DkF7p.js`) so the SHELL list can no
// longer enumerate them at SW build time. Strategy:
//   - Pre-cache the entry route + manifest + icons. That's it.
//   - Hashed Vite assets (the bundled CSS, the tiny entry JS, and the
//     lazy-loaded app chunk that carries MapLibre GL JS) are picked up
//     by NETWORK-FIRST on first navigate, then cached for offline
//     reloads. The dynamic-import map chunk (PR B2f) is just another
//     hashed /static/dist asset, so it's covered by the same rule.
//   - PR B2 swapped Leaflet for MapLibre GL JS (imported from npm and
//     Vite-bundled); there are no vendored map assets to cache.
const SHELL = [
  "/map",
  "/static/manifest.webmanifest",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

swSelf.addEventListener("install", (/** @type {ExtendableEvent} */ e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => swSelf.skipWaiting())
  );
});

swSelf.addEventListener("activate", (/** @type {ExtendableEvent} */ e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => swSelf.clients.claim())
  );
});

// Immutable, content-stable assets: safe to serve cache-first.
/** @param {string} pathname */
function isImmutable(pathname) {
  return pathname.startsWith("/static/vendor/") ||
         pathname.startsWith("/static/icons/");
}

/** @param {Request} req @returns {Promise<Response>} */
async function networkFirst(req) {
  try {
    const resp = await fetch(req);
    const copy = resp.clone();
    caches.open(CACHE).then((c) => c.put(req, copy));
    return resp;
  } catch (_) {
    const cached = (await caches.match(req)) || (await caches.match("/map"));
    return cached || Response.error();
  }
}

/** @param {Request} req */
async function cacheFirst(req) {
  const hit = await caches.match(req);
  if (hit) return hit;
  const resp = await fetch(req);
  const copy = resp.clone();
  caches.open(CACHE).then((c) => c.put(req, copy));
  return resp;
}

// Map data: serve the last response instantly, refresh in the background.
// The server already precomputes this, so "instant from cache, revalidate"
// makes a returning user's map paint before the network even answers.
/** @param {Request} req */
async function staleWhileRevalidate(req) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((resp) => {
      if (resp && resp.ok) cache.put(req, resp.clone());
      return resp;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

swSelf.addEventListener("fetch", (/** @type {FetchEvent} */ e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Self-hosted vector-basemap assets (style.json, sprite, glyph .pbf) live
  // cross-origin on R2. Cache them (stale-while-revalidate) so the vector base
  // can render offline. The big basemap.pmtiles archive is excluded on purpose
  // -- its range/206 reads are persisted at the byte level by offline-tiles.ts.
  if (url.pathname.includes("/basemap/") && !url.pathname.endsWith(".pmtiles")) {
    e.respondWith(staleWhileRevalidate(req));
    return;
  }
  if (url.origin !== location.origin) return;     // tiles / CDNs: passthrough
  if (
    url.pathname === "/api/rivers" ||
    url.pathname === "/api/river_lines" ||
    // /api/states is a stable catalog that gates the whole boot
    // (app-boot awaits it before the map init) — SWR removes the warm-
    // boot network stall (M1.5).
    url.pathname === "/api/states"
  ) {
    e.respondWith(staleWhileRevalidate(req));      // precomputed -> SWR
    return;
  }
  if (url.pathname.startsWith("/api/")) return;   // never cache other live data

  if (req.mode === "navigate") {
    e.respondWith(networkFirst(req));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      isImmutable(url.pathname) ? cacheFirst(req) : networkFirst(req)
    );
  }
});
