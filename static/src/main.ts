// Blueliner frontend entry point (Vite-managed).
//
// This file is the entry that Vite scans starting from
// `static/index.html`. Its job in PR B1a is to declare the asset
// graph -- pulling tokens.css, app.css, and Leaflet's CSS into the
// module graph so Vite fingerprints them in the production build.
//
// Roadmap:
//   - PR B1a (this PR): Vite live; CSS bundled + hashed. The actual
//     application code stays in static/app.js, loaded as a regular
//     script tag. Sets up the build pipeline.
//   - PR B1b+: Incremental extraction of static/app.js into typed
//     modules under static/src/{map,panels,data,ui,auth,catches}.
//     Each PR carves out one domain (e.g. extract `state.ts` with
//     STATES + deviceToken; extract `sparkline.ts` with the gauge
//     trend renderer). One module per PR keeps the diff small and
//     the regression surface tight.
//   - PR B2: actual Leaflet -> MapLibre swap once the module split
//     gives us seams to operate on layer-by-layer.

import "../tokens.css";
import "../app.css";
import "leaflet/dist/leaflet.css";

// Sentinel so we can confirm in DevTools that the Vite-bundled entry
// is loading. Logs once on first paint; harmless in production.
// eslint-disable-next-line no-console
console.info("[blueliner] vite entry loaded");
