# Path A reference — building stream/public-land PMTiles with tippecanoe

Sketch of the recommended build step. Runs in the **data-build** environment
(the same place `scripts/build_clickable_streams.py` runs — a laptop/CI with
the geo toolchain), **not** in the runtime Docker image. Output is a `.pmtiles`
archive uploaded to Cloudflare R2 next to the existing data artifacts; MapLibre
reads it via `pmtiles://` with HTTP range requests straight from R2 (a CDN).

## Why static tiles fit here

Streams (NHDPlus + state trout GIS) and public lands (PAD-US) are **static
reference data**, rebuilt on a data release, not per-user or live. tippecanoe
gives per-zoom simplification + feature-dropping for free, R2 already serves the
app's data with range support, and nothing touches the free web worker or Neon.

## Install tippecanoe (build env only)

```sh
# macOS
brew install tippecanoe
# Debian/CI
git clone https://github.com/felt/tippecanoe && cd tippecanoe && make -j && sudo make install
```

## Build streams.pmtiles

The build script already emits `data/nhdplus/clickable_streams.geojson.gz`.
Feed its decompressed form to tippecanoe:

```sh
gunzip -kc data/nhdplus/clickable_streams.geojson.gz > /tmp/clk.geojson

tippecanoe -o /tmp/streams.pmtiles \
  --layer=streams \
  --minimum-zoom=6 --maximum-zoom=14 \
  --drop-densest-as-needed \
  --coalesce-densest-as-needed \
  --simplification=4 \
  --no-tile-size-limit \
  --attribution="NHDPlus + state trout GIS" \
  --force \
  /tmp/clk.geojson
```

Notes:
- `--layer=streams` must match the client `source-layer` (see
  `mvt-client-patterns.ts`).
- Keep the styling/identity attributes (`levelpathid`, `gnis_name`,
  `streamorder`, `trout_class`); `levelpathid` is the `promoteId` for highlight.
- The per-zoom `min_order` filtering the API does today is replaced by
  tippecanoe's density-based dropping; if exact order-by-zoom control is wanted,
  pre-filter per zoom or use `--accumulate-attribute`. Validate against the
  current look before retiring the GeoJSON path.

## Build public_lands.pmtiles

```sh
gunzip -kc data/public_lands/public_lands.geojson.gz > /tmp/pl.geojson
tippecanoe -o /tmp/public_lands.pmtiles \
  --layer=public_lands \
  --minimum-zoom=6 --maximum-zoom=12 \
  --drop-densest-as-needed --coalesce-smallest-as-needed \
  --simplification=4 --force /tmp/pl.geojson
```

## Upload to R2 (next to the existing data artifacts)

```sh
# same bucket/prefix the app already fetches via DATA_BASE_URL
aws s3 cp /tmp/streams.pmtiles        s3://<bucket>/v1/streams.pmtiles        --endpoint-url "$R2_ENDPOINT"
aws s3 cp /tmp/public_lands.pmtiles   s3://<bucket>/v1/public_lands.pmtiles   --endpoint-url "$R2_ENDPOINT"
```

Confirm R2 returns `Accept-Ranges: bytes` (it does) — the pmtiles client relies
on HTTP 206 range requests to read the archive's directory + tiles. No app-server
involvement, so FastAPI `StaticFiles`' lack of range support is irrelevant.

## Cadence

Rebuild + upload on a data release (manual, or a scheduled CI job). Bump a
version in the URL (`/v1/…` → `/v2/…`) or rely on R2/CDN cache invalidation so
clients pick up a new archive.

## Client

`npm i pmtiles`, register the protocol once, point the vector source at
`pmtiles://https://<r2-host>/v1/streams.pmtiles` — see
`spike/mvt/mvt-client-patterns.ts`.
