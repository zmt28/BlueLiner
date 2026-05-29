#!/usr/bin/env bash
# Build the public-lands PMTiles archive (MVT M2, Path A) and optionally
# upload it to R2. Runs in the DATA-BUILD environment, NOT the runtime
# image — same place scripts/build_public_lands.py runs.
#
# Unlike streams, the public-lands GeoJSON is large (~100-300 MB) and is
# NOT committed to the repo — it's produced by build_public_lands.py and
# hosted on R2. So point this at the GeoJSON explicitly:
#
#   INPUT=/path/to/public_lands.geojson scripts/build_public_lands_tiles.sh
#   # or a gzipped input is auto-decompressed:
#   INPUT=/path/to/public_lands.geojson.gz scripts/build_public_lands_tiles.sh
#
# Upload (same R2 env vars as build_stream_tiles.sh):
#   R2_BUCKET=... R2_PREFIX=v1 R2_ENDPOINT=... AWS_ACCESS_KEY_ID=... \
#   AWS_SECRET_ACCESS_KEY=... INPUT=... scripts/build_public_lands_tiles.sh --upload
#
# Then set VITE_PUBLIC_LANDS_TILES_URL="${DATA_BASE_URL}/public_lands.pmtiles"
# at build (Render build env / docker build-arg) and redeploy. Unset = GeoJSON.
set -euo pipefail

OUT="${TMPDIR:-/tmp}/public_lands.pmtiles"
LAYER="public_lands"   # MUST match PUBLIC_LANDS_SOURCE_LAYER in static/src/config.ts
: "${INPUT:?set INPUT=/path/to/public_lands.geojson[.gz]}"

command -v tippecanoe >/dev/null || { echo "tippecanoe not found" >&2; exit 1; }
[ -f "$INPUT" ] || { echo "missing INPUT: $INPUT" >&2; exit 1; }

SRC="$INPUT"
if [[ "$INPUT" == *.gz ]]; then
  SRC="${TMPDIR:-/tmp}/public_lands.geojson"
  echo "==> decompressing $INPUT"
  gunzip -kc "$INPUT" > "$SRC"
fi

echo "==> tippecanoe -> $OUT"
# Polygons: drop/coalesce by area at low zooms; keep the styling/popup props
# (public_access drives the OA/RA fill+line `match`; unit_name/manager_name/
# designation/state_nm feed the popup). Match the GeoJSON look before retiring
# the /api/public_lands path.
tippecanoe -o "$OUT" \
  --layer="$LAYER" \
  --minimum-zoom=6 --maximum-zoom=12 \
  --drop-densest-as-needed \
  --coalesce-smallest-as-needed \
  --simplification=4 \
  --no-tile-size-limit \
  --attribution="PAD-US 4.0" \
  --force \
  "$SRC"

ls -lh "$OUT"

if [ "${1:-}" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  PREFIX="${R2_PREFIX:-v1}"
  echo "==> uploading to s3://$R2_BUCKET/$PREFIX/public_lands.pmtiles"
  aws s3 cp "$OUT" "s3://$R2_BUCKET/$PREFIX/public_lands.pmtiles" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type application/octet-stream
  echo "==> done. Set VITE_PUBLIC_LANDS_TILES_URL=<public-url>/public_lands.pmtiles and redeploy."
fi
