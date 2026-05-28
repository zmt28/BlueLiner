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
const CACHE = "blueliner-v20";
// Vite production builds emit fingerprinted asset filenames (e.g.
// `/static/dist/assets/index-DkF7p.js`) so the SHELL list can no
// longer enumerate them at SW build time. Strategy:
//   - Pre-cache the entry route + manifest + icons + the legacy
//     app.js (still the application code in PR B1a; PR B1b splits
//     it into TS modules; PR B2 swaps Leaflet -> MapLibre).
//   - Hashed Vite assets are picked up by the NETWORK-FIRST shell
//     handler on first navigate, then cached for offline reloads.
//   - tokens.css / app.css are no longer cached standalone -- in
//     production the served HTML references only the Vite-bundled
//     hashed CSS, which network-first picks up on first request.
const SHELL = [
  "/map",
  "/static/app.js",
  "/static/manifest.webmanifest",
  "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet/leaflet.js",
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
  if (url.origin !== location.origin) return;     // tiles / CDNs: passthrough
  if (url.pathname === "/api/rivers" || url.pathname === "/api/river_lines") {
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
