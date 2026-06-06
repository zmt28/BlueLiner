#!/usr/bin/env bash
# Build the VECTOR BASEMAP PMTiles archive and optionally upload it to R2.
# Runs in the DATA-BUILD environment (a laptop/CI with the geo toolchain) —
# NOT in the runtime Docker image, same as build_stream_tiles.sh.
#
# Unlike streams/public-lands (which tippecanoe builds from our own GeoJSON),
# the basemap is a regional *extract* of the Protomaps daily planet build,
# which is already published in the Protomaps v4 basemap schema that
# static/src/basemap.ts styles (source-layers earth/water/roads/places...).
# `pmtiles extract` pulls just our bbox over HTTP range requests, so we never
# download the whole planet.
#
# THE OFFLINE POINT: this single file is exactly what a mobile build bundles or
# downloads to the device and reads via file:// — same file, same schema, same
# basemap.ts theme on web, iOS, and Android. No per-load tile billing.
#
# Prereqs:
#   - pmtiles    (go-pmtiles: https://github.com/protomaps/go-pmtiles/releases)
#   - awscli     (for the R2 upload; or upload by hand)
#
# Usage:
#   # Default bbox = the mid-Atlantic around the app's default center.
#   scripts/build_basemap_tiles.sh                       # -> /tmp/basemap.pmtiles
#   BBOX=-80.6,37.2,-74.9,40.6 MAXZOOM=14 \
#     scripts/build_basemap_tiles.sh                     # custom region
#   R2_BUCKET=blueliner-data R2_PREFIX=v1 \
#   R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
#     scripts/build_basemap_tiles.sh --upload            # build + upload
#
# After uploading, flip the client over by setting the build-time env
#   VITE_BASEMAP_TILES_URL="${DATA_BASE_URL}/basemap.pmtiles"
# (Render build env / docker build-arg) and redeploy. Unset = raster base only.
set -euo pipefail

OUT="${TMPDIR:-/tmp}/basemap.pmtiles"
# Source: the Protomaps daily planet build (basemap v4 schema). Override SOURCE
# to pin a specific date, e.g. https://build.protomaps.com/20260601.pmtiles.
SOURCE="${SOURCE:-https://build.protomaps.com/$(date -u +%Y%m%d).pmtiles}"
# Default region: mid-Atlantic (VA/MD/PA/WV), the launch states. minLon,minLat,maxLon,maxLat
BBOX="${BBOX:--80.6,37.2,-74.9,40.6}"
MAXZOOM="${MAXZOOM:-14}"

command -v pmtiles >/dev/null || { echo "pmtiles CLI not found (go-pmtiles)" >&2; exit 1; }

echo "==> pmtiles extract $SOURCE"
echo "    bbox=$BBOX maxzoom=$MAXZOOM -> $OUT"
pmtiles extract "$SOURCE" "$OUT" \
  --bbox="$BBOX" \
  --maxzoom="$MAXZOOM"

ls -lh "$OUT"

if [ "${1:-}" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  PREFIX="${R2_PREFIX:-v1}"
  echo "==> uploading to s3://$R2_BUCKET/$PREFIX/basemap.pmtiles"
  aws s3 cp "$OUT" "s3://$R2_BUCKET/$PREFIX/basemap.pmtiles" \
    --endpoint-url "$R2_ENDPOINT" \
    --content-type application/octet-stream
  echo "==> done. Confirm the object returns 'Accept-Ranges: bytes' (R2 does),"
  echo "    then set VITE_BASEMAP_TILES_URL=<public-url>/basemap.pmtiles and redeploy."
fi
