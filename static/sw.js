// Blueliner service worker -- offline app shell only.
// Live data (/api/*) and map tiles are always fetched from the network.
//
// App code (app.js/app.css/manifest) and navigations are NETWORK-FIRST so
// deploys propagate immediately (cache-first here meant returning browsers
// ran stale JS forever). Only the immutable vendored Leaflet + icons are
// cache-first. Bump CACHE on any caching-strategy change.
const CACHE = "blueliner-v7";
const SHELL = [
  "/map",
  "/static/app.css",
  "/static/app.js",
  "/static/manifest.webmanifest",
  "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet/leaflet.js",
  "/static/icons/icon-180.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Immutable, content-stable assets: safe to serve cache-first.
function isImmutable(pathname) {
  return pathname.startsWith("/static/vendor/") ||
         pathname.startsWith("/static/icons/");
}

async function networkFirst(req) {
  try {
    const resp = await fetch(req);
    const copy = resp.clone();
    caches.open(CACHE).then((c) => c.put(req, copy));
    return resp;
  } catch (_) {
    return (await caches.match(req)) || (await caches.match("/map"));
  }
}

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

self.addEventListener("fetch", (e) => {
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
