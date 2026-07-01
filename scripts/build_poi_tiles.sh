#!/usr/bin/env bash
# Build a POINT-overlay PMTiles archive (access / dams / stocking) from a
# gzipped GeoJSON FeatureCollection and optionally upload it to R2. Runs in the
# DATA-BUILD environment (the geo toolchain box / CI), NOT the runtime Docker
# image -- same place the GeoJSON producers (build_river_poi.py, build_dams.py,
# build_stocking.py) run.
#
# These overlays are static reference data, rebuilt on a data release. The
# output is served straight from R2 via pmtiles:// range requests; the app
# server is never in the tile path (that's the whole point -- it retires the
# in-RAM /api/access|dams|stocking overlays that were OOMing the 512 MB free
# instance). Generalizes scripts/build_trail_tiles.sh for point layers.
#
# Prereqs:
#   - tippecanoe
#   - awscli (for --upload; or upload by hand)
#   - the source GeoJSON (gzipped)
#
# Usage:
#   scripts/build_poi_tiles.sh <layer> <src.geojson.gz> [--upload]
#     <layer>        MVT layer name; MUST match the *_SOURCE_LAYER in
#                    static/src/config.ts (access | dams | stocking)
#     <src.geojson.gz>  gzipped GeoJSON FeatureCollection of Point features
#
#   R2_BUCKET=... R2_ENDPOINT=... R2_PREFIX=v4 AWS_ACCESS_KEY_ID=... \
#   AWS_SECRET_ACCESS_KEY=... scripts/build_poi_tiles.sh access \
#     data/access_points/access.geojson.gz --upload
#
# After uploading, flip the client over with the build-time env
#   VITE_ACCESS_TILES_URL="${DATA_BASE_URL}/access.pmtiles"   (etc.)
# (Render build env / docker build-arg) and redeploy. Unset = layer not added.
set -euo pipefail

LAYER="${1:?usage: build_poi_tiles.sh <layer> <src.geojson.gz> [--upload]}"
SRC_GZ="${2:?missing source .geojson.gz}"
UPLOAD="${3:-}"

OUT="${TMPDIR:-/tmp}/${LAYER}.pmtiles"
SRC_JSON="${TMPDIR:-/tmp}/${LAYER}.geojson"

command -v tippecanoe >/dev/null || { echo "tippecanoe not found" >&2; exit 1; }
[ -f "$SRC_GZ" ] || { echo "missing $SRC_GZ" >&2; exit 1; }

echo "==> decompressing $SRC_GZ"
gunzip -kc "$SRC_GZ" > "$SRC_JSON"

echo "==> tippecanoe -> $OUT (layer=$LAYER)"
# Point overlays are a zoom-in detail layer (you only look for a boat ramp once
# you're reading a river), so they start at zoom 8; --drop-densest-as-needed
# thins dense metros at low zoom while keeping every point by z14. The popup /
# styling props (name/type/access/source/precision/notes/agency_url/water/
# species/season/river/owner/...) ride along; source_id + levelpathid are
# build-only join keys the client never reads, so drop them to shrink tiles.
tippecanoe -o "$OUT" \
  --layer="$LAYER" \
  --minimum-zoom=8 --maximum-zoom=14 \
  --base-zoom=12 \
  --drop-densest-as-needed \
  --extend-zooms-if-still-dropping \
  --no-tile-size-limit \
  --exclude=source_id \
  --exclude=levelpathid \
  --force \
  "$SRC_JSON"

ls -lh "$OUT"

if [ "$UPLOAD" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  PREFIX="${R2_PREFIX:-v4}"
  echo "==> uploading to s3://$R2_BUCKET/$PREFIX/${LAYER}.pmtiles"
  aws s3 cp "$OUT" "s3://$R2_BUCKET/$PREFIX/${LAYER}.pmtiles" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type application/octet-stream
  echo "==> done. Set VITE_${LAYER^^}_TILES_URL=<public-url>/${LAYER}.pmtiles and redeploy."
fi
