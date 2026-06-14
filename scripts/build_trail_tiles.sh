#!/usr/bin/env bash
# Build the river-trails PMTiles archive from data/trails/trails.geojson.gz
# and optionally upload it to R2. Runs in the DATA-BUILD environment (the
# geo toolchain box / CI), NOT the runtime Docker image -- same place
# scripts/build_trails.py runs.
#
# Trails are static reference data, rebuilt on a data release. The output is
# served from R2 via pmtiles://; the app server is never in the tile path.
#
# Prereqs:
#   - tippecanoe
#   - awscli (for --upload; or upload by hand)
#   - the source GeoJSON: data/trails/trails.geojson.gz (run build_trails.py)
#
# Usage:
#   scripts/build_trail_tiles.sh                 # build only -> $TMPDIR/trails.pmtiles
#   R2_BUCKET=bluelines-data R2_PREFIX=v4 \
#   R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
#     scripts/build_trail_tiles.sh --upload      # build + upload
#
# After uploading, flip the client over with the build-time env
#   VITE_TRAILS_TILES_URL="${DATA_BASE_URL}/trails.pmtiles"
# (Render build env / docker build-arg) and redeploy. Unset = no trail layer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_GZ="$ROOT/data/trails/trails.geojson.gz"
SRC_JSON="${TMPDIR:-/tmp}/trails.geojson"
OUT="${TMPDIR:-/tmp}/trails.pmtiles"
LAYER="trails"   # MUST match TRAILS_SOURCE_LAYER in static/src/config.ts

command -v tippecanoe >/dev/null || { echo "tippecanoe not found" >&2; exit 1; }
[ -f "$SRC_GZ" ] || { echo "missing $SRC_GZ (run build_trails.py first)" >&2; exit 1; }

echo "==> decompressing $SRC_GZ"
gunzip -kc "$SRC_GZ" > "$SRC_JSON"

echo "==> tippecanoe -> $OUT"
# Trails are a zoom-in detail layer (they only matter once you're reading a
# river), so start at zoom 9; drop the densest as needed to keep low zooms
# light. name/trail_type/surface/length_mi ride along for the popup.
tippecanoe -o "$OUT" \
  --layer="$LAYER" \
  --minimum-zoom=9 --maximum-zoom=14 \
  --drop-densest-as-needed \
  --coalesce-densest-as-needed \
  --simplification=4 \
  --no-tile-size-limit \
  --attribution="USGS The National Map -- National Digital Trails" \
  --force \
  "$SRC_JSON"

ls -lh "$OUT"

if [ "${1:-}" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  PREFIX="${R2_PREFIX:-v1}"
  echo "==> uploading to s3://$R2_BUCKET/$PREFIX/trails.pmtiles"
  aws s3 cp "$OUT" "s3://$R2_BUCKET/$PREFIX/trails.pmtiles" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type application/octet-stream
  echo "==> done. Set VITE_TRAILS_TILES_URL=<public-url>/trails.pmtiles and redeploy."
fi
