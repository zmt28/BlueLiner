# MVT (vector tile) spike findings — streams + public lands

Spike to evaluate serving Blueliner's **clickable streams** and **public lands**
as Mapbox Vector Tiles (MVT) instead of the current per-viewport GeoJSON
fetches. Mirrors the B2 spike format: grounded reference code + measurements +
a phasing recommendation. Nothing here is wired into the app.

## TL;DR

1. **The data is static reference data, and Cloudflare R2 is already in the
   stack** — so the natural fit is **static PMTiles built by tippecanoe and
   served from R2** (a CDN with HTTP range support). This bypasses the free
   single-worker app server *and* Neon entirely, works at every zoom, and needs
   **no PostGIS migration**. This is the recommendation.
2. **Geometry today is GeoJSON *text* in plain Postgres (Neon), not PostGIS.**
   The GiST index is on a built-in `box`, not a `geometry` column. So
   `ST_AsMVT` is **not** directly available — the "textbook" Postgres path
   requires enabling PostGIS + a geometry-column migration + reload-pipeline
   changes. Given the data is static, you don't need dynamic DB tiling at all.
3. **Measured win (real MD data, 103,610 reaches):** a dense z8 tile is
   **115 KB GeoJSON (12 KB gz) → 9 KB MVT (4.5 KB gz)** — ~2.6× smaller
   gzipped, ~12× raw; deeper tiles are sub-KB. Plus the structural wins:
   per-tile URL caching (CDN + SW), no zoom-9 floor, no 4° bbox cap, and MVT
   decodes off the main thread.
4. **Three paths, ranked for *this* app:** (A) static PMTiles on R2 —
   **recommended**; (B) dynamic Python MVT endpoint (no PostGIS, reuses the
   existing query) — good fallback; (C) PostGIS `ST_AsMVT` — not now.

## What the current system does (grounding)

- **Storage:** `clickable_streams` / `public_lands` tables store geometry as
  **GeoJSON `TEXT`** with precomputed `min/max_lon/lat` columns. Postgres has a
  GiST index on a generated `box` (`idx_clk_bbox_gist`); queries use the `&&`
  overlap operator. **No PostGIS** (`requirements.txt` has `shapely` + `psycopg`
  only; no `ST_*` anywhere). (`db.py` `query_clickable_streams` /
  `query_public_lands`.)
- **Serving:** `/api/clickable_streams?bbox=&zoom=` returns a GeoJSON
  FeatureCollection, capped at **4000** features, with a per-zoom
  `min_order` filter (`_min_order_for_zoom`) and a **4° bbox cap**; client
  zoom-gated at **≥9**. `/api/public_lands` is analogous (cap 500, min-zoom 9).
  Both cache via `Cache-Control: s-maxage` (Cloudflare edge). (`main.py`.)
- **Client (post-B2):** one GeoJSON source + line layers per dataset;
  `source.setData()` on each viewport `moveend`; the "blink across zoom-9",
  debounced refetch, and re-apply-highlight-after-`setData` all exist to work
  around viewport GeoJSON. (`streams.ts`, `map-layers.ts`.)
- **Data is static reference data:** streams come from NHDPlus + state trout GIS
  via `scripts/build_clickable_streams.py` → `data/nhdplus/clickable_streams.geojson.gz`
  (~6 MB gz / 49 MB raw / 103,610 reaches); public lands from PAD-US via
  `scripts/build_public_lands.py` (~290K parcels). Rebuilt on data releases, not
  per-user, not live.
- **Infra:** Render **free** web service, **single worker** (`WEB_CONCURRENCY=1`),
  **ephemeral disk**, external **Neon free** Postgres. Large data artifacts are
  already hosted on **Cloudflare R2** and fetched at boot (`DATA_BASE_URL`,
  `data_source.py`). FastAPI `StaticFiles` does **not** support HTTP range
  requests (a PMTiles blocker *only if* served from the app — see below).

## Measurements (real data, `spike/mvt/measure_tiles.py`)

Clickable streams over a dense Maryland area, comparing the current per-tile
GeoJSON payload vs an MVT-encoded tile (same per-zoom `min_order` filter):

| tile  | feats | GeoJSON | GeoJSON gz | MVT  | MVT gz | encode |
|-------|------:|--------:|-----------:|-----:|-------:|-------:|
| z8    |   333 | 115 KB  |   11.9 KB  | 9.2 KB | 4.5 KB | 43 ms |
| z10   |    85 |  31 KB  |    3.8 KB  | 2.6 KB | 1.7 KB | 11 ms |
| z12   |    12 | 4.8 KB  |    0.8 KB  | 0.5 KB | 0.5 KB |  2 ms |
| z13   |     4 | 2.3 KB  |    0.6 KB  | 0.3 KB | 0.3 KB |  1 ms |

Takeaways: MVT is meaningfully smaller (most at low zoom, where the current
4000-cap viewport response is largest); encode is cheap except on the densest
low-zoom tiles (43 ms cold, single worker). With tiles being **URL-cacheable**
(CDN + service worker), cold encodes are rare — and with **static PMTiles**,
encode time is a build-time cost, not a request cost.

## The three paths

### A. Static PMTiles on R2 (tippecanoe) — RECOMMENDED

Build a `.pmtiles` archive from the GeoJSON we already produce, upload to R2
next to the existing data artifacts; MapLibre reads it via the `pmtiles://`
protocol with HTTP range requests **directly against R2** (a CDN).

- **Pros:** zero runtime cost on the free web worker + Neon (tiles served from
  R2/CDN, not the app); works at *every* zoom (tippecanoe does per-zoom
  simplification + feature-dropping); URL-cacheable + offline-cacheable; **no
  PostGIS**; reuses the existing build-pipeline + R2 muscle; the `StaticFiles`
  no-range blocker is moot because tiles aren't served by the app.
- **Cons:** adds `tippecanoe` (a C++ tool) to the **data-build** step (not the
  runtime image — same place `build_clickable_streams.py` lives); a rebuild +
  upload cadence (fine — data is static); +1 small client dep (`pmtiles`).
- **Fit:** streams + public lands are exactly the static, occasionally-rebuilt
  reference layers PMTiles is designed for.

### B. Dynamic Python MVT endpoint (no PostGIS) — good fallback

A `/tiles/streams/{z}/{x}/{y}.pbf` FastAPI route that reuses the **existing GiST
bbox query**, clips with shapely, and encodes with `mapbox-vector-tile`
(see `spike/mvt/mvt_server_dynamic.py`). Cache via `Cache-Control: s-maxage`
(Cloudflare edge) + a service-worker `/tiles/*` strategy.

- **Pros:** no new infra, no PostGIS, reuses the current schema + index +
  `_min_order_for_zoom`; ships as a pure-Python add (`mapbox-vector-tile`,
  `mercantile`); cacheable by URL (unlike the current bbox responses).
- **Cons:** on a cache-miss the **single free worker** does the 1–43 ms encode
  (serialized under concurrency); Python encode is slower than `ST_AsMVT`.
  Mitigated by edge caching (static-ish data → high hit rate).
- **When:** if you want the tiling wins without a tippecanoe build dependency,
  or expect the data to become more dynamic.

### C. PostGIS `ST_AsMVT` — not now

Enable PostGIS on Neon, add a `geometry` column (populate via
`ST_GeomFromGeoJSON` at load), GiST-index it, then one `ST_AsMVT` query per
tile.

- **Pros:** fastest per-tile generation (native C, single query),
  `ST_AsMVTGeom` clipping + `ST_SimplifyPreserveTopology` per zoom.
- **Cons:** PostGIS extension + **geometry-column migration** + reworking
  `db.py` load + `build_*` scripts; pushes per-request CPU onto the **free
  Neon** instance behind a single web worker. The data is static, so dynamic DB
  tiling is unnecessary overhead.
- **When:** only if streams/lands become dynamic or per-user.

## Client wire-up (see `spike/mvt/mvt-client-patterns.ts`)

Switching `streams.ts` (and `map-layers.ts` public lands) from a GeoJSON source
to a vector source is small and preserves the B2 patterns:

- Source becomes `{ type: "vector", url: "pmtiles://…" }` (static) or
  `{ type: "vector", tiles: ["/tiles/streams/{z}/{x}/{y}.pbf"] }` (dynamic).
  Layers gain a `"source-layer"` (the MVT layer name, e.g. `"streams"`).
- The **two-layer visible+hit** pattern, the `match`/`interpolate` paint
  expressions, the color-mode `setPaintProperty`, and the click→gauged/ungauged
  flow are **unchanged**.
- **Highlight:** keep feature-state with `promoteId: "levelpathid"`. Caveat —
  vector-tile features only exist for *loaded* tiles, and a reach crossing tile
  boundaries appears as multiple features sharing `levelpathid` (so the id-based
  highlight still covers all parts). The name-spanning-multiple-levelpaths case
  uses the same `querySourceFeatures` + set-state-per-match loop as today,
  re-applied on the `"sourcedata"` event as new tiles load (the tile analogue of
  re-apply-after-`setData`).
- **Removed once parity is confirmed:** the zoom-9 gate, the 4° bbox cap, the
  `moveend` debounced refetch, and the manual `setData` plumbing — MapLibre
  fetches/caches/decodes tiles itself.
- `levelpathid`/`comid` are ≤ ~10⁹ → safe as float64 feature ids.

## Recommended phasing

- **M1 — Streams to PMTiles.** Add a tippecanoe build step
  (`scripts/build_stream_tiles.sh`, `spike/mvt/build_pmtiles.md`) producing
  `streams.pmtiles`; upload to R2 alongside the current artifacts. Switch
  `streams.ts` to the vector source behind a flag (keep the GeoJSON path for
  rollback). Validate parity (color-mode, click→panel, ungauged card, highlight
  persists on pan) + re-measure. **Biggest single win.**
- **M2 — Public lands to PMTiles.** Same pipeline + `map-layers.ts` switch.
- **M3 — Retire the GeoJSON path.** Drop `/api/clickable_streams` +
  `/api/public_lands` bbox endpoints, the zoom-9 gates, the bbox-cap/debounce
  machinery. Optional follow-on: a vector base map (separate effort) now that
  the client + R2 tile-serving pattern exists.
- **Fallback toggle:** if a tippecanoe build dependency is unwanted, ship Path B
  (`mvt_server_dynamic.py`) instead at M1 — same client changes, different tile
  origin.

## Open decisions for the team

1. **R2 write access from the build pipeline** (where `build_*` already run) —
   confirm the upload path/credentials, same as the existing data artifacts.
2. **Tile rebuild cadence** — on data release (manual) vs a scheduled job.
   Streams/lands change rarely, so manual-on-release is likely fine.
3. **Path A vs B** — tippecanoe build dependency (A) vs single-worker encode
   cost (B). A is recommended given the static data + existing R2.

## Files in this spike

| File | Role |
|---|---|
| `MVT-SPIKE-FINDINGS.md` | this write-up |
| `measure_tiles.py` | the MVT-vs-GeoJSON measurement harness (reproducible) |
| `mvt_server_dynamic.py` | Path B reference: a no-PostGIS Python `/tiles` endpoint |
| `mvt-client-patterns.ts` | MapLibre vector-source client patterns (reference) |
| `build_pmtiles.md` | Path A reference: the tippecanoe → R2 build sketch |
