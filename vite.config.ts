import { defineConfig } from "vite";

// Vite config for the Blueliner frontend.
//
//   npm run dev    -> dev server on :5173 with HMR; /api and friends
//                     proxied to FastAPI on :8000 so the SPA + the
//                     Python backend feel like one origin.
//   npm run build  -> production bundle to `static/dist/`. FastAPI
//                     serves it from the `/static` mount; the
//                     `/map` route returns `static/dist/index.html`
//                     when present.
//
// Entry: `static/index.html` contains a `<script type="module"
// src="/src/main.ts">` tag. Vite scans it, bundles the TS module
// graph + every CSS file it imports, fingerprints the output, and
// writes a manifest the service worker reads to know which files
// to cache.
//
// PR B1 keeps Leaflet (npm dep). PR B2 swaps the renderer to
// MapLibre; this file stays the same.
export default defineConfig({
  root: "static",
  base: "/static/dist/",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: "static/index.html",
    },
  },
  server: {
    port: 5173,
    // FastAPI hosts the API + auth callback + service worker + the
    // vendored Leaflet + the static icons. In dev, Vite serves
    // index.html and the TS module graph; everything else falls
    // through to FastAPI via these proxies. Importantly /static
    // is proxied so `<script src="/static/app.js">`, the icon
    // manifest, and the legacy Leaflet vendor dir all resolve.
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/auth": { target: "http://localhost:8000", changeOrigin: true },
      "/internal": { target: "http://localhost:8000", changeOrigin: true },
      "/healthz": { target: "http://localhost:8000", changeOrigin: true },
      "/sw.js": { target: "http://localhost:8000", changeOrigin: true },
      "/static": { target: "http://localhost:8000", changeOrigin: true },
    },
  },
});
