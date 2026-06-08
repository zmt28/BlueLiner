#!/usr/bin/env node
/**
 * Generate a self-hosted MapLibre style.json for the BlueLiner vector basemap
 * (Phase 0 of the offline-ready vector basemap).
 *
 * Wraps protomaps-themes-base's layer generator, but points the vector source,
 * glyphs, and sprite at OUR R2 prefix instead of protomaps.github.io -- so the
 * basemap is fully self-hosted (no third-party runtime dependency, the same
 * goal that motivated moving off the CARTO/Esri/USGS raster tile servers) and
 * every asset is offline-cacheable next to streams.pmtiles.
 *
 * Not bundled into the app. Runs in the data-build env (Node), driven by
 * scripts/build_basemap_tiles.sh. Standalone:
 *   npm i --no-save protomaps-themes-base@4.5.0
 *   node scripts/gen_basemap_style.mjs \
 *     --source protomaps --theme light --lang en \
 *     --tiles  'pmtiles://https://data.blueliner.app/v5/basemap.pmtiles' \
 *     --glyphs 'https://data.blueliner.app/v5/basemap/fonts/{fontstack}/{range}.pbf' \
 *     --sprite 'https://data.blueliner.app/v5/basemap/sprites/v4/light' \
 *     --out    /tmp/style.json
 */
import { writeFileSync } from "node:fs";

// Resolve protomaps-themes-base from an explicit path when given (ESM ignores
// NODE_PATH, so the build script installs it into a scratch dir and points here
// via PMT_THEMES_PATH). Falls back to normal bare resolution for `npm i` setups.
let layers;
try {
  ({ layers } = await import(process.env.PMT_THEMES_PATH || "protomaps-themes-base"));
} catch (e) {
  console.error(
    "gen_basemap_style: cannot load protomaps-themes-base.\n" +
      "  Install it and retry, e.g.:\n" +
      "    npm i --no-save protomaps-themes-base@4.5.0\n" +
      "  or set PMT_THEMES_PATH to its ESM entry (…/dist/esm/index.js).\n" +
      `  (${e.message})`,
  );
  process.exit(1);
}

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : def;
}

const source = arg("source", "protomaps");
const theme = arg("theme", "light");
const lang = arg("lang", "en");
const tiles = arg("tiles");
const glyphs = arg("glyphs");
const sprite = arg("sprite");
const out = arg("out", "static/basemap/style.json");

for (const [k, v] of Object.entries({ tiles, glyphs, sprite })) {
  if (!v) {
    console.error(`gen_basemap_style: missing --${k}`);
    process.exit(1);
  }
}

// protomaps-themes-base 4.x: layers(sourceName, themeName, { lang }) returns the
// full ordered layer stack already bound to `source: <sourceName>`. We supply
// the matching vector source below.
const style = {
  version: 8,
  name: `BlueLiner Basemap (Protomaps ${theme})`,
  glyphs,
  sprite,
  sources: {
    // url is a pmtiles:// URL; the app registers the pmtiles protocol
    // (static/src/tiles.ts, ensurePmtilesProtocol) which resolves it to a
    // TileJSON + range-read tiles, exactly like streams.pmtiles.
    [source]: {
      type: "vector",
      url: tiles,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: layers(source, theme, { lang }),
};

writeFileSync(out, JSON.stringify(style, null, 1));
console.error(
  `gen_basemap_style: wrote ${out} (${style.layers.length} layers, theme=${theme}, lang=${lang})`,
);
