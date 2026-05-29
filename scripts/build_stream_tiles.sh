#!/usr/bin/env bash
# Build the clickable-stream PMTiles archive (MVT spike, Path A) and
# optionally upload it to R2. Runs in the DATA-BUILD environment (a
# laptop/CI with the geo toolchain) — NOT in the runtime Docker image,
# same place scripts/build_clickable_streams.py runs.
#
# Streams are static reference data, so this is run on a data release,
# not per-request. The output is served from R2 (a CDN with HTTP range
# support) via the pmtiles:// protocol; the app server + DB are never in
# the tile path.
#
# Prereqs:
#   - tippecanoe        (brew install tippecanoe  |  build from felt/tippecanoe)
#   - awscli            (for the R2 upload; or upload by hand)
#   - the source GeoJSON: data/nhdplus/clickable_streams.geojson.gz
#
# Usage:
#   scripts/build_stream_tiles.sh                # build only -> /tmp/streams.pmtiles
#   R2_BUCKET=blueliner-data R2_PREFIX=v1 \
#   R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
#     scripts/build_stream_tiles.sh --upload      # build + upload to R2
#
# After uploading, flip the client over by setting the build-time env
#   VITE_STREAM_TILES_URL="${DATA_BASE_URL}/streams.pmtiles"
# (Render build env / docker build-arg) and redeploy. Unset = GeoJSON path.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_GZ="$ROOT/data/nhdplus/clickable_streams.geojson.gz"
SRC_JSON="${TMPDIR:-/tmp}/clk.geojson"
OUT="${TMPDIR:-/tmp}/streams.pmtiles"
LAYER="streams"   # MUST match STREAM_SOURCE_LAYER in static/src/config.ts

command -v tippecanoe >/dev/null || { echo "tippecanoe not found" >&2; exit 1; }
[ -f "$SRC_GZ" ] || { echo "missing $SRC_GZ (run build_clickable_streams.py first)" >&2; exit 1; }

echo "==> decompressing $SRC_GZ"
gunzip -kc "$SRC_GZ" > "$SRC_JSON"

echo "==> tippecanoe -> $OUT"
# --drop-densest-as-needed keeps low zooms light (the per-zoom min_order the
# GeoJSON API did by hand); levelpathid/gnis_name/streamorder/trout_class are
# preserved for styling + the promoteId highlight. Validate the look against
# the GeoJSON path before retiring it.
tippecanoe -o "$OUT" \
  --layer="$LAYER" \
  --minimum-zoom=6 --maximum-zoom=14 \
  --drop-densest-as-needed \
  --coalesce-densest-as-needed \
  --simplification=4 \
  --no-tile-size-limit \
  --attribution="NHDPlus + state trout GIS" \
  --force \
  "$SRC_JSON"

ls -lh "$OUT"

if [ "${1:-}" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  PREFIX="${R2_PREFIX:-v1}"
  echo "==> uploading to s3://$R2_BUCKET/$PREFIX/streams.pmtiles"
  aws s3 cp "$OUT" "s3://$R2_BUCKET/$PREFIX/streams.pmtiles" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type application/octet-stream
  echo "==> done. Confirm the object returns 'Accept-Ranges: bytes' (R2 does),"
  echo "    then set VITE_STREAM_TILES_URL=<public-url>/streams.pmtiles and redeploy."
fi
