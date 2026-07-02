# Multi-stage build. Stage 1 produces the Vite frontend bundle
# (static/dist/); stage 2 is the lean Python runtime that copies
# the built assets in. The final image has no Node runtime.
#
# Added in PR #71 (hotfix). Before this, PRs #68/#69 moved CSS +
# app.js into the Vite module graph but the Dockerfile only ran
# `pip install` -- so production fell back to source index.html
# which referenced /src/main.ts (a dev-only path) and the live
# app loaded with no CSS and no JS. This file fixes that.

# -- Stage 1: build the frontend bundle ------------------------------
FROM node:20-alpine AS frontend

WORKDIR /app

# Copy lockfiles first so the npm cache layer is reusable across
# rebuilds where only source changed (npm ci re-runs only when
# package.json / package-lock.json change).
COPY package.json package-lock.json ./
RUN npm ci

# Then the source Vite needs: index.html + src/ + the loose CSS
# files (tokens.css, app.css) + maplibre-gl's CSS imported via the npm
# package. tsconfig is read by Vite for path resolution; vite.config
# defines the entry + base path.
COPY vite.config.ts tsconfig.json ./
COPY static/ ./static/

# Optional MVT cutover (Path A): set these to the public R2 URLs of the
# PMTiles archives to render streams / public lands from vector tiles
# instead of the GeoJSON endpoints. Unset = keep the GeoJSON path. Vite
# inlines VITE_* env at build time, so they must be ARG/ENV here. Render
# can supply them as build-time env vars / docker build-args.
ARG VITE_STREAM_TILES_URL=""
ENV VITE_STREAM_TILES_URL=$VITE_STREAM_TILES_URL
ARG VITE_PUBLIC_LANDS_TILES_URL=""
ENV VITE_PUBLIC_LANDS_TILES_URL=$VITE_PUBLIC_LANDS_TILES_URL
ARG VITE_BASEMAP_TILES_URL=""
ENV VITE_BASEMAP_TILES_URL=$VITE_BASEMAP_TILES_URL
ARG VITE_TRAILS_TILES_URL=""
ENV VITE_TRAILS_TILES_URL=$VITE_TRAILS_TILES_URL
# Point overlays as static PMTiles (retire the in-RAM /api/{access,dams,
# stocking} endpoints). Unset = layer not added.
ARG VITE_ACCESS_TILES_URL=""
ENV VITE_ACCESS_TILES_URL=$VITE_ACCESS_TILES_URL
ARG VITE_DAMS_TILES_URL=""
ENV VITE_DAMS_TILES_URL=$VITE_DAMS_TILES_URL
ARG VITE_STOCKING_TILES_URL=""
ENV VITE_STOCKING_TILES_URL=$VITE_STOCKING_TILES_URL
ARG VITE_FLYSHOPS_TILES_URL=""
ENV VITE_FLYSHOPS_TILES_URL=$VITE_FLYSHOPS_TILES_URL
# Client-side search index (M4.2): gauges + counties + towns JSON.
# Unset = river-catalog-only search.
ARG VITE_SEARCH_INDEX_URL=""
ENV VITE_SEARCH_INDEX_URL=$VITE_SEARCH_INDEX_URL

# Build -> static/dist/index.html + static/dist/assets/*. The dist
# folder is what stage 2 copies in below; everything else in this
# stage is discarded.
RUN npm run build


# -- Stage 2: Python runtime -----------------------------------------
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# shapely/psycopg[binary] ship manylinux wheels with their native libs
# bundled -- no apt build/runtime deps needed on slim. (geopandas is a
# build-only dep now; the app uses shapely directly to stay lean.)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Overlay the built frontend bundle on top of the source tree. The
# `static/dist/` from stage 1 is what main.py's /map endpoint serves
# in production (it falls back to source static/index.html only when
# dist/ is absent, i.e. dev mode).
COPY --from=frontend /app/static/dist ./static/dist

RUN useradd -m app && chown -R app /app
USER app

EXPOSE 8000

# Render provides $PORT. WEB_CONCURRENCY tunes workers; default 1. Now that
# geopandas is gone the per-worker baseline is ~30MB (was ~106MB), so the
# free tier has room to raise this if needed.
CMD ["sh", "-c", "gunicorn main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --timeout 120 --access-logfile - --error-logfile -"]
