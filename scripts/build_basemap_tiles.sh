#!/usr/bin/env bash
# Build the BlueLiner vector BASEMAP archive (Phase 0 of the offline-ready
# vector basemap) and optionally publish it to R2 alongside streams.pmtiles.
#
# Unlike build_stream_tiles.sh (which renders OUR NHDPlus GeoJSON with
# tippecanoe), the basemap tiles come from the public Protomaps planet build
# (OpenStreetMap data, ODbL). We do NOT download the whole planet: `pmtiles
# extract` issues HTTP range requests against the remote archive and pulls only
# the CONUS pyramid, so this fits a free CI runner (a few hundred MB, minutes).
#
# Everything is mirrored under the same versioned R2 prefix the app already
# uses, so the basemap is fully self-hosted (no runtime dependency on
# protomaps.github.io) and every asset is offline-cacheable:
#
#   <prefix>/basemap.pmtiles                       vector tiles
#   <prefix>/basemap/style.json                    MapLibre style (self-referencing)
#   <prefix>/basemap/fonts/<stack>/<range>.pbf     glyphs (labels)
#   <prefix>/basemap/sprites/v4/<theme>.{png,json} icon atlas
#
# Prereqs (data-build env: a laptop/CI with network + the geo toolchain) --
# NOT the runtime Docker image, same as build_stream_tiles.sh:
#   - pmtiles CLI   (https://github.com/protomaps/go-pmtiles releases)
#   - node + npm    (generates the style via protomaps-themes-base)
#   - git, awscli
#
# Usage:
#   SOURCE_URL=https://build.protomaps.com/20260601.pmtiles \
#   PUBLIC_BASE=https://data.blueliner.app/v5 \
#     scripts/build_basemap_tiles.sh                 # build only -> $TMPDIR/bl-basemap
#
#   SOURCE_URL=... PUBLIC_BASE=https://data.blueliner.app/v5 \
#   R2_BUCKET=bluelines-data R2_PREFIX=v5 \
#   R2_ENDPOINT=https://<acct>.r2.cloudflarestorage.com \
#   AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
#     scripts/build_basemap_tiles.sh --upload        # build + publish to R2
#
# IMPORTANT: PUBLIC_BASE's version segment MUST match R2_PREFIX (both `v5`
# above) -- the style.json bakes absolute PUBLIC_BASE URLs for its own tiles,
# glyphs, and sprite, and they're served from the prefix you upload to.
#
# After uploading, wire the client (Phase 1) by setting the build-time env
#   VITE_BASEMAP_TILES_URL="${PUBLIC_BASE}/basemap.pmtiles"
# and redeploying. Unset = the vector base option simply isn't offered.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${TMPDIR:-/tmp}/bl-basemap"
OUT="$WORK/basemap.pmtiles"
STYLE="$WORK/style.json"
ASSETS="$WORK/basemaps-assets"

# CONUS (lower-48), matching the app's NHDPlus coverage. AK/HI are excluded
# exactly like the rest of BlueLiner's data. minLon,minLat,maxLon,maxLat.
BBOX="${BBOX:--125.0,24.4,-66.9,49.4}"
MAXZOOM="${MAXZOOM:-15}"     # Protomaps planet maxzoom; the style overzooms past it
THEME="${THEME:-light}"      # protomaps-themes-base theme: light|dark|white|black|grayscale
LANG="${LANG_:-en}"
SOURCE_NAME="protomaps"      # vector source id; must match in the generated style
PMT_THEMES_VERSION="${PMT_THEMES_VERSION:-4.5.0}"

: "${SOURCE_URL:?set SOURCE_URL to a Protomaps planet build, e.g. https://build.protomaps.com/YYYYMMDD.pmtiles}"
: "${PUBLIC_BASE:?set PUBLIC_BASE to the public https base incl. version segment, e.g. https://data.blueliner.app/v5}"
command -v pmtiles >/dev/null || { echo "pmtiles CLI not found (go-pmtiles)" >&2; exit 1; }
command -v node    >/dev/null || { echo "node not found" >&2; exit 1; }
command -v git     >/dev/null || { echo "git not found" >&2; exit 1; }

mkdir -p "$WORK"

echo "==> extracting CONUS pyramid from $SOURCE_URL (range requests, not a full download)"
# Verify flag names against `pmtiles extract --help` on your CLI version.
pmtiles extract "$SOURCE_URL" "$OUT" --bbox="$BBOX" --maxzoom="$MAXZOOM"
ls -lh "$OUT"

echo "==> fetching Protomaps fonts + sprites (basemaps-assets)"
rm -rf "$ASSETS"
git clone --depth 1 https://github.com/protomaps/basemaps-assets "$ASSETS"

echo "==> generating style.json (protomaps-themes-base@$PMT_THEMES_VERSION, theme=$THEME)"
# Install into the scratch dir (no repo node_modules pollution) and point the
# generator at the ESM entry directly -- bare `import` + NODE_PATH does not work
# for ESM, so PMT_THEMES_PATH is an absolute path.
( cd "$WORK" && npm init -y >/dev/null 2>&1 && \
  npm i --no-save "protomaps-themes-base@$PMT_THEMES_VERSION" >/dev/null 2>&1 )
PMT_THEMES_PATH="$WORK/node_modules/protomaps-themes-base/dist/esm/index.js" \
node "$ROOT/scripts/gen_basemap_style.mjs" \
  --source "$SOURCE_NAME" --theme "$THEME" --lang "$LANG" \
  --tiles  "pmtiles://$PUBLIC_BASE/basemap.pmtiles" \
  --glyphs "$PUBLIC_BASE/basemap/fonts/{fontstack}/{range}.pbf" \
  --sprite "$PUBLIC_BASE/basemap/sprites/v4/$THEME" \
  --out    "$STYLE"
ls -lh "$STYLE"

if [ "${1:-}" = "--upload" ]; then
  : "${R2_BUCKET:?set R2_BUCKET}" "${R2_ENDPOINT:?set R2_ENDPOINT}"
  command -v aws >/dev/null || { echo "awscli not found" >&2; exit 1; }
  PREFIX="${R2_PREFIX:-v1}"
  base="s3://$R2_BUCKET/$PREFIX"
  ep=(--endpoint-url "$R2_ENDPOINT")

  echo "==> uploading basemap.pmtiles -> $base/basemap.pmtiles"
  aws s3 cp "$OUT" "$base/basemap.pmtiles" "${ep[@]}" --content-type application/octet-stream

  echo "==> uploading style.json"
  aws s3 cp "$STYLE" "$base/basemap/style.json" "${ep[@]}" --content-type application/json

  echo "==> uploading fonts (glyph .pbf)"
  aws s3 cp "$ASSETS/fonts" "$base/basemap/fonts" --recursive "${ep[@]}" \
    --content-type application/x-protobuf

  echo "==> uploading sprites (png + json, two passes for correct content-type)"
  aws s3 cp "$ASSETS/sprites" "$base/basemap/sprites" --recursive "${ep[@]}" \
    --exclude "*" --include "*.png" --content-type image/png
  aws s3 cp "$ASSETS/sprites" "$base/basemap/sprites" --recursive "${ep[@]}" \
    --exclude "*" --include "*.json" --content-type application/json

  echo "==> done. Confirm $PUBLIC_BASE/basemap.pmtiles returns 'Accept-Ranges: bytes',"
  echo "    then set VITE_BASEMAP_TILES_URL=$PUBLIC_BASE/basemap.pmtiles and redeploy (Phase 1)."
fi
