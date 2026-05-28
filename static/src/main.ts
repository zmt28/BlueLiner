// Blueliner frontend entry point (Vite-managed).
//
// Imports CSS for Vite's asset graph, then the canonical TS modules
// (each future module extraction lands here), then the legacy
// app.js as a side-effect module. Order matters: TS modules with
// window-side-effect bridges (state.ts, future util.ts, etc.) must
// run BEFORE app.js so its globals are populated when app.js tries
// to read them.
//
// Roadmap:
//   - PR B1a: Vite pipeline live. (shipped)
//   - PR B1b (this PR): app.js converted to a Vite-managed module;
//     state.ts extracted as the first canonical TS module that
//     app.js imports from. Pattern established for B1c+.
//   - PR B1c+: Incrementally extract util.ts, sparkline.ts,
//     map-setup.ts, ... -- one domain per PR.
//   - PR B2: Leaflet -> MapLibre swap on the now-modularized seams.

import "../tokens.css";
import "../app.css";
import "leaflet/dist/leaflet.css";

// Canonical TS modules. Each one assigns to window on import for the
// legacy app.js bridge (until app.js is itself fully extracted into
// these modules and the window assignments can be dropped). Import
// order: leaf utilities first, modules that depend on them next.
import "./state";
import "./util";
import "./sparkline";
import "./map-setup";
import "./map-layers";
import "./snap-sheet";
import "./river-panel";
import "./streams";

// Legacy application code, now a Vite-managed module so it can `import`
// from sibling TS modules. Vite bundles it into the production output;
// no separate <script src="/static/app.js"> tag in index.html anymore.
import "./app.js";
