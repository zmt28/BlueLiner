# MapLibre GL JS spike findings (B2 prep)

This is a write-up of what I learned standing up MapLibre in isolation
against the Blueliner data model. Goal: answer the open questions the
original plan flagged, and propose concrete phasing for B2.

## TL;DR

1. **MapLibre 5.24.0 maps cleanly to every Leaflet pattern Blueliner
   uses.** No nasty surprises. Each of the 13 patterns in the spike has
   a clear 1:1 or near-1:1 replacement.
2. **Bundle cost: +240 KB gzipped** (Leaflet 44 KB → MapLibre 285 KB).
   Real, but mitigable via dynamic-import lazy-loading of the map
   chunk.
3. **The biggest footgun is coordinate order**: MapLibre uses
   `[lng, lat]`, Leaflet uses `[lat, lng]`. Every coord site needs the
   flip. A `riverLngLat(r)` helper at the boundary lets us grep one
   place for correctness.
4. **Phasing**: 5 sub-PRs (B2a → B2e), each scoped to one renderer
   concern with a clear validation point. Same shape as B1.

## What I exercised

Reference TS implementations in `maplibre-patterns.ts`, covering:

1. Map setup + raster base style (CARTO / Esri / USGS Topo as raster sources)
2. Base-map switching (rebuild style + re-attach overlays)
3. GeoJSON layers (line/fill, data-driven paint expressions)
4. Color-mode toggle for clickable streams (`setPaintProperty`)
5. Click handlers (layer-scoped, `map.on('click', layerId)`)
6. Selection highlight via `feature-state` + paint expression `case`
7. HTML markers (`new maplibregl.Marker({element})`)
8. Popups (`maplibregl.Popup`)
9. Tooltip emulation (~30 lines: `mousemove` + shared popup)
10. Layer visibility via `setLayoutProperty(id, 'visibility', ...)`
11. Viewport queries (`getBounds`, `getZoom`, `contains`, `fitBounds`)
12. `moveend` listener (identical API)
13. Service worker (no changes needed)

All compile cleanly against project types + `@types/maplibre-gl`. Vite
bundles to 1.05 MB raw / 285 KB gzipped (the spike-dist build verifies
this).

## Concrete findings

### 1. Coordinate order is the #1 footgun

Every `L.marker([r.lat, r.lon])` becomes
`new maplibregl.Marker(...).setLngLat([r.lon, r.lat])`.
Every `b.contains([r.lat, r.lon])` becomes `b.contains([r.lon, r.lat])`.

**Mitigation**: Add a `riverLngLat(r: River): [number, number]` helper
in `util.ts`. Use it everywhere a River turns into a coordinate. One
place to grep when something renders 4000 km southeast of where it
should.

### 2. Base-map switching gotcha

`map.setStyle(newStyle, { diff: true })` drops user-added sources/
layers unless you re-attach them in a `style.load` event listener.

**Mitigation**: Move all source/layer setup into one
`attachOverlays(map)` function called from both `setupMap()` and
`map.on('style.load', attachOverlays)`. Slightly more complex than
Leaflet's "just keep your layer references."

### 3. GeoJSON re-render is **faster** in MapLibre

`source.setData(newFC)` updates the GL buffer in one shot. Leaflet's
`L.geoJSON.addData(fc)` creates per-feature DOM `<path>` elements.
At 5-10k visible features (typical for clickable streams in a state
viewport), MapLibre paints in a single GL draw call vs. Leaflet's
5-10k DOM nodes.

We won't *notice* this until clickable streams are migrated (B2c) but
it's a real win.

### 4. Selection highlight is cleaner via feature-state

Today's streams.ts does:
```js
clickableVisible.eachLayer(l => {
  if (matchesKey(l.feature)) l.setStyle({color: '#e74c3c', weight: 8});
});
```
And has to re-apply after every `loadClickableStreams` fetch (because
new L.geoJSON layers don't carry the selection forward).

MapLibre's feature-state persists across `source.setData` when
`promoteId` is set:
```js
map.setFeatureState({source, id}, {selected: true});
```
Paint expression reads `["feature-state", "selected"]` and updates
automatically. No more "re-apply highlight after each fetch."

### 5. Bundle: +240 KB gzipped

Measured in the spike-dist build:
```
spike-bundle.js   1,053.72 kB │ gzip: 284.78 kB
```
Current Blueliner: 192 KB raw / 58 KB gzipped (with Leaflet bundled).

After swap: ~960 KB raw / ~298 KB gzipped (Leaflet exits ~140 KB raw
/ 44 KB gzipped; MapLibre arrives ~1.05 MB raw / 285 KB gzipped).

**Mitigation**: dynamic import the map chunk:
```ts
// main.ts
const { setupMap } = await import("./map-setup");
```
Initial paint (header + state selector skeleton) is unblocked while
the map JS downloads. ~60 KB gzipped for the shell vs. waiting on
~300 KB. The deferred chunk caches once + reused across sessions.

This is the same pattern Vite uses for route-split chunks. Vite's
manifest tracks the lazy chunk separately so the service worker can
include it in SHELL pre-cache once on first navigate.

### 6. HTML markers vs GL symbol layers

For Blueliner's marker counts (50-150 condition + access markers in a
typical state view, plus < 100 saved pins), HTML markers via
`new maplibregl.Marker({element})` perform the same as Leaflet divIcons
-- both use CSS transforms outside the WebGL canvas.

At 1000+ markers MapLibre's symbol layers become dramatically faster
(GPU rendered) but require a sprite atlas. We don't need it for B2.
**Defer GL symbol layers to a hypothetical B3 scaling phase** if/when
marker counts grow.

### 7. Tooltip emulation is ~30 lines

MapLibre has no `bindTooltip`. The `createTooltipHelper` in the spike
shows the pattern: one shared `maplibregl.Popup`, position it on
`mousemove` over the layer, hide on `mouseleave`. ~30 lines of code,
typed, reusable.

For touch devices add `touchmove` listener (+5 lines). Test on actual
mobile during B2a verification.

### 8. Two-layer visible+hit pattern still works

Today's `clickableVisible` (non-interactive thin line) +
`clickableHit` (transparent 16 px casing for touch) carry directly to
MapLibre as two GL line layers off the same source. Order matters:
hit layer ABOVE visible so the click event hits it.

```js
map.addLayer({ id: 'visible', type: 'line', source: 'streams', paint: { 'line-opacity': 0.8 } });
map.addLayer({ id: 'hit',     type: 'line', source: 'streams', paint: { 'line-opacity': 0, 'line-width': 16 } });
```

### 9. Vector tiles deferred (no change from original plan)

Spike kept all base maps as raster sources wrapped in a MapLibre
raster style. This works today; the migration to vector tiles
(Protomaps / OpenFreeMap / MapTiler / self-hosted) is a separate
project worth weeks of its own. Documented in the original plan as
Phase 3.

## Proposed phasing

5 sub-PRs, each scoped to one renderer concern. Same model as B1.

### B2a — Map setup + foundation (~250 lines)

- Vendor `maplibre-gl` + `@types/maplibre-gl` via npm
- Rewrite `map-setup.ts`: `new maplibregl.Map` + raster base style
- New helpers: `popups.ts` (popup wrapper + tooltip helper),
  `coords.ts` (riverLngLat + bbox helpers)
- One simple overlay migrated end-to-end: `river-lines` as a GeoJSON
  source + line layer. Validates: source.setData, paint expression,
  click event, feature-state highlight.
- Other layers (trout, access, public-lands, clickable streams)
  temporarily skipped: their `ensureX` fetchers run but their data
  goes to no-op stubs. App is **partially functional**: base map +
  river lines + UI chrome work; trout/access toggles paint nothing.
- This is the riskiest landing because it's the first cut. Keep PR
  description honest: "App is degraded until B2b lands."

### B2b — Rivers + river panel (~350 lines)

- `rivers.ts`: condition markers via HTML markers, render loop, the
  per-river `riverLineBySite` cache flow
- `river-panel.ts`: highlight via `setFeatureState` on the river-lines
  source instead of `layer.setStyle`
- `controls.ts`: base-map segment + layer toggles use
  `setLayoutProperty` for visibility
- After B2b, gauged rivers work end-to-end (markers + lines + panel +
  highlight). Trout/access/public-lands still no-op.

### B2c — Clickable streams (~400 lines)

- `streams.ts`: the entire highlight state machine, color-mode toggle,
  viewport-bounded fetch, two-layer visible+hit pattern, ungauged-
  card flow. This is the most complex single migration.
- Validate the perf claim from finding #3 with the real Maryland or
  Pennsylvania clickable-streams data load.

### B2d — Overlays (~200 lines)

- `map-layers.ts`: trout, access, public-lands all become GeoJSON
  sources + line/fill layers. Per-tier styling via `match` expression.
  Public-lands click popups.
- After B2d, every map feature works.

### B2e — Pins + Leaflet exit (~150 lines)

- `pins.ts`: saved-pin HTML markers + delete popup
- `package.json`: remove `leaflet` + `@types/leaflet`
- `types.d.ts`: remove the `_blRiver` declaration-merge
- `leaflet-augment.d.ts`: delete
- Every module: drop `import * as L from "leaflet"`
- The Leaflet exit is what FINALLY recovers the bundle size
  (Leaflet ~44 KB gzipped goes away). Net post-B2e: ~285 + 58 - 44 =
  ~299 KB gzipped.

## What's NOT in B2

Per the original plan, **these are deferred to Phase 3** and beyond:

- Vector tile base maps (Protomaps OSS self-host etc.)
- GL symbol layers for high-density markers
- Server-side vector tile encoding for streams + public-lands
- Shared style.json with the future Flutter mobile app

## Risks worth calling out per sub-PR

- **B2a**: setStyle gotcha (#2). The `attachOverlays` pattern needs to
  be right from the start or every base-map switch loses river lines.
- **B2b**: coordinate flip footgun (#1). I'll add the `riverLngLat`
  helper as the first commit and use it everywhere.
- **B2c**: tooltip code path on TOUCH devices. Validate on phone.
- **B2d**: setStyle reattachment again — public-lands re-attach needs
  the same listener.
- **B2e**: leaflet-augment.d.ts deletion may surface stale `_blRiver`
  references elsewhere. Run typecheck after each module's L exit.

## Recommendation

Start with B2a. Will produce a degraded-but-working app that proves
the foundation. We get real signal on: (a) does the bundle size feel
OK in practice, (b) does the lazy-load pattern work cleanly with
Vite, (c) is the coordinate-flip helper sufficient at preventing the
flip footgun.
