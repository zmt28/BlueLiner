// BlueLines service worker -- offline app shell only.
// Live data (/api/*) and map tiles are always fetched from the network.
const CACHE = "bluelines-v1";
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

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;        // tiles / CDNs: passthrough
  if (url.pathname.startsWith("/api/")) return;      // never cache live data

  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/map")));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    e.respondWith(
      caches.match(req).then((hit) =>
        hit ||
        fetch(req).then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return resp;
        })
      )
    );
  }
});
