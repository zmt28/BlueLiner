# BlueLiner architecture review — July 2026

**Quality bar:** TroutRoutes (onX). **Constraints:** ≤ ~$25/mo infra + licensing, web/PWA only,
nationwide (lower-48) ambition. **Priorities (per product owner):** map feel & polish, product
features, in that order; data breadth/model second.

Method: four parallel audits — map rendering path, frontend/product architecture, backend +
data model, and a web-research benchmark of TroutRoutes' 2025–26 feature set — synthesized
here. File references are to this repo at the time of review.

---

## 1. Executive summary

**The gap versus TroutRoutes is not architectural.** The architecture — MapLibre GL with
incremental layers, static PMTiles on R2 for all geometry, precomputed condition snapshots in
Postgres, declarative per-state source registries — is the *right* design for a nationwide app
on a free-tier budget, and in places (trout classification model, serverless tiles) it is
better suited to the constraint than TroutRoutes' Mapbox + licensed-data approach.

**The single biggest finding: the work that closes the "smooth / nice" gap is already built
and dark-shipped.** The self-hosted Protomaps vector basemap (professional cartography,
self-hosted glyphs/sprites, CONUS extract pipeline in `scripts/build_basemap_tiles.sh`) and
the full offline-download system (IndexedDB byte-range PMTiles cache, viewport download flow,
storage persistence, offline cold-start) are both complete in code and **disabled in
production** because `VITE_BASEMAP_TILES_URL` has never been set (`config.ts`, `Dockerfile:38`,
absent from `render.yaml`). Production users get 256 px CARTO raster tiles with a 0.85-opacity
USGS hydro raster painted on top — which is most of why the map looks soft, muddy, and
double-drawn next to TroutRoutes.

Second-order findings:

- **Product-feature gaps are concentrated in three places:** offline maps (built, off — above),
  search (a client-side river-name filter, despite UI promising counties/gauges), and the
  stickiness loop (favorites → condition alerts → notifications) which is roadmap-only with
  zero code. Everything else (river panel, sparklines, elevation profile, catch log with
  auto-enrichment, snap-sheet mobile UX) is genuinely competitive.
- **The data model's trout classification is the strongest asset** — a 3-axis model
  (tier gold/1/2/3 × wild × native) that is arguably richer than TroutRoutes' flat Class 0–3,
  fully declarative, geometry-joined to NHDPlus, covering 21 states + 9 range-wide native
  overlays. It is under-sold by the current cartography.
- **The real nationwide bottleneck is human curation hours per state**, not compute or
  architecture. The registry + seeds + endpoint-watch + coverage-survey pipeline is the right
  machine; it just needs feeding.
- **Where TroutRoutes is weak** (per user complaints): access/parking accuracy on the ground,
  stale/wrong classifications, a nearly useless free tier, and price resentment post-onX.
  BlueLiner's live conditions scoring — which TroutRoutes only approximates with a raw gauge
  layer — is the natural differentiator, and it's currently buried.

**Recommended next step (Phase 1, ~$0, days of work):** light up the vector basemap and
offline downloads, kill the hydro-raster-over-everything default, and fix the layer-ordering
contract. That one release closes most of the perceived map-quality gap. Phases 2–4 below
build from there.

---

## 2. Benchmark: TroutRoutes in 2025–26 (condensed)

Acquired by onX **March 2024**. iOS/Android + Pro-only desktop web map
(maps.troutroutes.com). Mapbox-lineage cartography, now onX basemaps + 3D terrain.

| Area | TroutRoutes | BlueLiner today |
|---|---|---|
| Stream classification | Proprietary Class 0–3 (Class 0 = Gold/Blue Medal), ~50k streams, 48 states; blends access, length, state quality class | Tier gold/1/2/3 + wild + native from state agency data; 21 states + 9 native-range overlays; transparent, auditable |
| Access | ~350k access points; signature **public/private bridge-crossing layer**; trailheads, parking, camping, fly shops | 5-type access points (ramp/walk-in/pier/parking/wading) from 29 state feeds + OSM/RIDB; no bridge layer; thin attributes |
| Land | Public/private boundaries (onX parcel machinery); offline-capable. Parcel-level owner names unconfirmed in TR | PAD-US public-lands overlay, deliberately coarse (~900 m simplification, binary open/restricted) |
| Live data | USGS gauge layer w/ history charts (post-2024); weather; journal-based hatch content | **Scored** conditions (flow-vs-median + temp → green/yellow/red per river), hatch schedules, enrichment — richer than TR here |
| Offline | Pro: custom offline regions incl. land data | Fully built, dark-shipped |
| Tools | River-miles calculator, elevation/gradient charts, tailwater ID, regulations (Pro), custom waypoints, cross-device sync | Elevation profile ✓, pins ✓, catch log ✓; no distance tool, no regulations, no sync-first UX |
| Pricing | Free tier ≈ teaser; Pro ~$58.99/yr (single-state $19.99/yr) | Free |
| Known weaknesses | Wrong/missing parking + access on the ground; stale classifications; crashes/login bugs; paywall resentment | — (differentiation targets) |

---

## 3. Findings

### 3.1 Map rendering & cartography (top priority)

**Sound foundations.** Streams are true vector tiles (PMTiles source, `promoteId`, feature-state
for selection) — there is no `setData` anywhere, so the classic GeoJSON-payload jank class is
absent. POIs are GPU symbol layers with canvas-rasterized icons, not DOM markers; only the
selected river's gauge discs and user pins are DOM. The never-call-`setStyle` base-swap design
(`map-setup.ts`) correctly preserves overlays and feature-state across basemap switches.

**P0 — vector basemap built but off.** `addVectorBase()` is fully wired (style fetch, glyphs,
sprite, layer injection with generation guard); the style generator wraps
`protomaps-themes-base` — professional-grade cartography. Gated on `VITE_BASEMAP_TILES_URL`,
which defaults to `""` in the `Dockerfile` and is not set in `render.yaml`, so the UI tile is
removed and production is CARTO-raster-only. `build_basemap_tiles.sh` already does a CONUS
`pmtiles extract` (a few hundred MB — pennies/month on R2) and mirrors style/glyphs/sprites to
the versioned R2 prefix. The script's own header documents the one missing step.

**P0 — the hydro raster degrades every basemap.** `USGSHydroCached` raster at 0.85 opacity is
on by default and anchored *above* the base — so it paints over the (future) vector base's
labels, and today it double-renders water: raster blue linework misaligned under the
tier-colored vector streams. Two sets of blue lines that don't agree is a large share of the
"doesn't look as nice." With a vector base (which has its own water), hydro should default off
or be dropped.

**P1 — no terrain.** TroutRoutes ships 3D terrain/hillshade; anglers read gradient. MapLibre
`raster-dem` + free keyless AWS Terrarium tiles (`elevation-tiles-prod`) gives hillshade (and
optionally `setTerrain`) at $0. This is the single highest look-per-dollar addition after the
vector base.

**P1 — layer ordering has no contract.** Overlays mount with no `beforeId`; z-order depends on
`onMapReady` registration order *and* promise timing (POI layers land after an icon-load
`.then()`). Works today, breaks silently under refactor. A single-style world wants named
anchors (e.g. insert line overlays below the base's label layers, symbols above).

**P1 — pan-time jank source identified.** On every `sourcedata` event for the streams source
(fires repeatedly per gesture), `reapplyStreamHighlight()` + `_applyCondOverlay()` run
`querySourceFeatures` over the whole source layer and `setFeatureState` per feature — O(all
loaded reaches) during active panning, worst with a condition overlay active on dense networks
(`streams.ts:317–518`). Throttle to `idle`, scope to newly-loaded tiles, or precompute.

**P2 — polish details.** 256 px raster tiles are soft on HiDPI (vector base fixes this).
POI symbols use `icon-allow-overlap: true` → clutter at mid-zoom with ~92k dams; let the
collision engine work or add `symbol-sort-key`. Icons are flat Lucide-on-disc — serviceable,
below TR's cartographic iconography. `EMPTY_STYLE.glyphs` points at third-party
demotiles.maplibre.org (inert today, a trap for any future label).

### 3.2 Product features & PWA

**Offline (P0):** the IndexedDB byte-range cache (`offline-tiles.ts`) is real — header/directory
ranges cached so offline *cold start* works, explicit-download-only writes, storage-persist
request, viewport download flow with tile-count preflight. All gated behind the same unset env
var. Today's production "offline" = app shell + stale `/api/rivers` + **blank basemap** (raster
tiles are cross-origin and pass through the SW uncached). Shipping this is a headline Pro-tier
TroutRoutes feature at $0.

**Search (P1):** `search.ts` is a substring filter over the in-memory river catalog. The input
placeholder promises "rivers, gauges, counties…". No geocoding, no gauge-ID, no place search.
A static client-side index (rivers + gauges + counties + towns from free Census/GNIS data,
prebuilt to JSON on R2) delivers 90% of the value with zero runtime cost; a free geocoder
(Photon/Nominatim within usage policy) is the fallback for arbitrary places.

**Stickiness loop (P1):** no favorites, no alerts, no notifications — `PRODUCT.md` roadmap
items with zero code. This is where BlueLiner's actual differentiator (scored live conditions)
should compound: *favorite waters → "Gunpowder just went green" web push / Resend email*.
TroutRoutes cannot match this without building a scoring layer. Web Push API + existing magic-
link email infra ≈ $0. Accounts, pins, and the catch log already exist to hang this on.

**Boot path (P2):** warm boot is network-gated on an uncached `/api/states` (stable catalog —
SW-cache it or inline into the shell) and pulls Lucide from `unpkg.com` at runtime (bundle it
at build time; it's also a supply-chain exposure). The code-split (map chunk after first
paint) is correct and working.

**Mobile UX:** genuinely good — pointer-captured snap-sheet, notch-aware viewport, passive
touch listeners, debounced moveend. Two independent bottom-sheet implementations
(`snap-sheet.ts` vs `controls.ts:wireSheetDrag`) with drifting thresholds should merge.
Near-zero optimistic UI (every pin/catch write awaits the round trip) — cheap perceived-speed
wins available.

**Module architecture:** strict TS, clean code-split, but 49 `window.*` bridges across 14
files now exist solely to break import cycles (the legacy `app.js` they were built for is
gone). Untyped-at-callsite globals fail at runtime, not compile time. A small typed event
bus / registry module would retire most of them; low urgency, real fragility.

### 3.3 Data model

**Trout classification — keep and showcase.** Two orthogonal axes (`trout_class` agency
designation; `tier` gold/class1/class2/class3 quality) + `is_wild` + `is_native`, normalized
via five declarative source modes in `data/trout/sources.json` with a pure, unit-tested engine
(`trout_registry.py`). COMID attachment distrusts agency COMIDs (NHD-vintage drift) and does a
~100 m buffered spatial join against NHDPlusV2; per-river harmonization prevents green/grey
fragmentation. The 9 range-wide native overlays (EBTJV brook trout, StreamNet/TU cutthroat,
bull, redband, Gila) are the budget-smart move — they backfill enormous territory from free
CC-BY data. Honest gaps, already documented in the rubric: eastern tiers are coarser
(state class × stream order, not biomass/access composites); gold thresholds are uncalibrated
knobs; native overlays paint whole layers wild. None of these need architectural change —
they need per-state curation hours and, eventually, a validation pass against a few
states' own quality rankings.

**Access points — the model is thinner than the competition and thinner than the data
allows.** Schema is `{name, type(5), access(public/private), source, precision, levelpathid}`.
No amenities, no parking capacity, no seasonal notes, no verified-on-the-ground signal.
TroutRoutes' complaint file (wrong parking, missing well-known access) says accuracy — not
volume — is the open flank: a `verified`/user-report field and a feedback loop would
differentiate at $0 licensing. **Biggest missing entity: the bridge-crossing access layer** —
TroutRoutes' signature. It is computable from open data (OSM road/stream crossings ∩ public
land or road right-of-way heuristics) as a build-time PMTiles layer. Recommend adding it as a
first-class entity (`crossing`: road ref, stream, land-ownership context) rather than a sixth
access-point type.

**Public lands — coarse by design; stay coarse.** Six PAD-US fields, ~900 m simplification,
binary open/restricted, inholdings dropped. Correct call: parcel data at onX quality is a
multi-$100k/yr licensing game — do not chase it. Two cheap improvements: state the semantics
in the UI ("uncolored ≠ private, it's unknown"), and consider a higher-zoom, less-simplified
variant of the same PAD-US build for z12+ (tiles are static; cost is R2 bytes, not compute).

**Stocking** — schema and per-source adapters are sound; 33 baselines + 18 live feeds, graceful
degradation, proximity tagging probes gauges as well as centroids. No changes recommended
beyond coverage growth.

**Scoring** — transparent and defensible (temp bands; flow vs day-of-year median; worst gauge
wins) but coarse: no turbidity, no gauge-height fallback for temp-less sites, no trend ("rising
fast") signal. The precompute pipeline already has everything needed to add a trend arrow and
a "fishable window" forecast — high angler value, zero new data cost.

### 3.4 Backend & serving

**The serving split is right and recently completed:** immutable geometry → static PMTiles on
R2 read straight from CDN (streams, access, dams, stocking, public lands, trails); volatile
low-cardinality conditions → precomputed Postgres snapshots refreshed out-of-band; the only
dynamic map-path call is `/api/rivers?bbox=`. In-RAM national overlays were retired. This is
exactly the ≤$25/mo nationwide architecture.

**`main.py` (2,397 lines) is a god module** — routes, USGS/NLDI clients, assembly, scoring,
popup-HTML rendering, auth, catches — with lazy circular imports from `precompute.py` as the
tell. Mechanical split (`clients.py`, `scoring.py`, `popup.py`, `routes_*.py`) is overdue but
behavior-neutral; schedule it, don't block product work on it.

**Server-rendered `popup_html` in JSON payloads** is the coupling wart with a perf cost: every
river in every `/api/rivers` response carries a rendered HTML string. Moving popup rendering
client-side (data already present in the payload) shrinks the fattest response and unblocks
future payload caching. Do it as part of the `main.py` split.

**Dual SQLite/Postgres** abstraction is earning its keep (zero-dep tests, Neon prod) — keep.
Operational risks, both known: Render free-tier cold starts (keep-warm mitigates; the $7/mo
Starter instance is the first thing to buy if TTFB hurts) and the hard dependency on external
Neon for snapshot durability.

**Data pipeline / registries** — the sources.json + seeds + endpoint-watch + coverage-survey
machine is well-designed and report-only promotion is the right safety posture. The
throughput ceiling is human field-mapping judgment per state (~21/50 states on trout, 29/50 on
access, 33+18 on stocking). Treat coverage growth as scheduled curation labor (e.g. two
states/month off the COVERAGE.md worklist), not as an engineering problem to re-solve.

---

## 4. Recommendations — phased next steps

All phases fit the ≤$25/mo envelope; estimated *incremental* cost is noted per phase.
Order reflects the stated priorities (map feel → product features → data model → hygiene).

### Phase 1 — Light up what's built (~$0/mo incremental; days)
1. **Ship the vector basemap.** Run `build_basemap_tiles.sh` (CONUS extract → R2), set
   `VITE_BASEMAP_TILES_URL` on Render, make `vector` the default base, keep rasters as options.
2. **Default the USGS hydro overlay off** (or auto-off when the vector base is active). Ends
   double-drawn water and label-smothering in one line.
3. **Ship offline downloads** — same flag; the UI already self-mounts. Verify the SW asset
   caching path on a real device; consider small download-concurrency (sequential today).
4. **Establish a layer-order contract:** explicit `beforeId` anchors (overlays below base
   labels, symbols above), removing the promise-timing dependency.
5. Boot hygiene: SW-cache or inline `/api/states`; bundle Lucide instead of unpkg.

### Phase 2 — Cartography & smoothness pass (~$0–1/mo; 1–2 weeks)
6. **Hillshade** via free AWS Terrarium `raster-dem` (optionally MapLibre `setTerrain` for 3D).
7. **Make tier the hero:** restyle stream colors/widths around gold/1/2/3 + wild on the vector
   base; add along-stream name labels (self-hosted glyphs already exist). This is where the
   data model's strength becomes visible product quality.
8. **Fix the pan-time hotspot:** move highlight/condition-overlay reapplication off
   `sourcedata` to `idle`/throttled, or scope to new tiles.
9. POI polish: enable symbol collision (drop `icon-allow-overlap`) with `symbol-sort-key`;
   one icon-design pass.

### Phase 3 — Product stickiness (~$0/mo; weeks, sequenced)
10. **Favorites → alerts → web push.** Favorite waters on the existing accounts/pins plumbing;
    "went green / blew out" alerts via Web Push + Resend email. This weaponizes the scoring
    engine TroutRoutes lacks and is the retention loop.
11. **Real search:** prebuilt static index (rivers, gauges, counties, towns) fetched once and
    searched client-side; free geocoder fallback for arbitrary places.
12. Perceived-speed pass: optimistic pin/catch writes, skeletons on panel loads.
13. Scoring depth: trend arrows ("rising fast") and a simple fishable-window signal from data
    already in the snapshot pipeline.

### Phase 4 — Data model & platform hygiene (ongoing)
14. **Bridge-crossing access layer** from OSM crossings × PAD-US/right-of-way heuristics, as a
    new first-class entity built into PMTiles — the open-data answer to TroutRoutes' signature
    layer.
15. Access-point accuracy loop: `verified` provenance + in-app "report this spot" feedback;
    richer attributes where source fields carry them.
16. Coverage as cadence: promote from COVERAGE.md at a fixed rate (e.g. 2 states/month/dataset).
17. Split `main.py`; move popup rendering client-side; merge the two bottom-sheet
    implementations; retire window bridges behind a typed event bus.
18. Higher-zoom PAD-US variant (z12+, less simplification). Do **not** pursue parcel licensing.

### Explicit non-goals under current constraints
- Private-parcel/landowner data (licensing economics don't close at this budget).
- Native mobile apps (per product direction: web/PWA only).
- Matching TroutRoutes' 50k-stream curated count near-term — compete on live conditions,
  accuracy, openness, and a genuinely useful free product instead.

---

## 5. Cost check

Phase 1–3 incremental spend ≈ **$0–2/mo** (R2 storage for the CONUS basemap + terrain stays in
pennies; Terrarium tiles, Web Push, Photon, Resend free tier all $0). First paid upgrade worth
making if/when needed: Render Starter ($7/mo) to kill cold starts — still leaves >$15/mo
headroom under the cap. The scarce resource is curation hours, not dollars; the phasing above
spends engineering time where the product owner said it hurts: map feel first.
