# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

### Frontend (Vite + TypeScript)
```bash
npm run dev          # Dev server on :5173, proxies /api /auth /static /sw.js to FastAPI :8000
npm run build        # Production bundle → static/dist/
npm run typecheck    # TypeScript validation (no emit)
```

### Backend (FastAPI + Python 3.11)
```bash
uvicorn main:app --reload          # Dev server on :8000
pytest -q                          # Run all tests
pytest -q tests/test_foo.py        # Run a single test file
pytest -q tests/test_foo.py::test_bar  # Run a single test
python scripts/validate_data.py    # Lint stocking/access/trout/hatch JSON schemas
python scripts/verify_feed_sources.py  # End-to-end check of the live ArcGIS feed
                                       # registries (--candidates probes unpromoted
                                       # leads). Run from CI or a dev machine -- the
                                       # Claude Code sandbox egress blocks most state
                                       # GIS hosts (a push touching the registries
                                       # triggers .github/workflows/gis-endpoint-verify.yml)
```

### Data Pipeline (one-time builds, not routine dev)
```bash
python scripts/build_nhdplus_vaa.py       # NHDPlus routing attributes
python scripts/build_clickable_streams.py # Stream network geometry
python scripts/build_public_lands.py      # PAD-US public lands overlay
```

### Docker (production build)
```bash
docker build -t blueliner .   # Multi-stage: node:20-alpine (frontend) → python:3.11-slim (backend)
```

## Architecture

BlueLiner is a real-time stream condition monitoring app for fly fishermen. It aggregates USGS water data, scores conditions, and presents them on an interactive map.

### Backend (`main.py` + modules)

`main.py` is the monolithic FastAPI app (~82 KB) containing all route handlers. Supporting modules:

- **`db.py`** — Dual SQLite/Postgres abstraction. Uses `DATABASE_URL` for Postgres, falls back to `BLUELINER_DB` (SQLite). Tables: `pins`, `accounts`, `sessions`, `catches`, `river_stats`, `gauge_meta`, `river_snapshot`, `nhdplus_vaa`
- **`precompute.py`** — Background loop (every 45 min) assembles per-state river snapshots into Postgres. Single-flighted to avoid double-ups from both in-process timer and GitHub Actions cron. Two-pass: data snapshot first, gauge_meta backfill second (batched concurrency=8)
- **`cache.py`** — `LruTtl` bounded in-memory cache. Used for USGS stats (~6K entries) and gauge metadata (~2K entries, 15-min TTL). Prevents OOM on free tier
- **`enrichment.py`** — Auto-enriches catch logs with flow-vs-median, water temp, NOAA weather, moon phase
- **`hatches.py`** — Hatch scheduling with zone-based lookup + curated overrides
- **`stocking.py`** — State stocking data with ~2 km proximity tagging. Curated baselines in `data/stocking/<ST>.json` (32 states) + live agency ArcGIS feeds declared in `data/stocking/sources.json` (per-source field mappings: `species_flags`, `species_field`, `dedupe`; unverified leads in `candidates.json`)
- **`access_points.py`** — Angler access points (boat ramps, walk-ins, piers, wading). Same pattern: per-state baselines + `data/access_points/sources.json` live-feed registry (`type_field`/`type_flags`/`fixed_type`)
- **`trout.py`** — Trout stream overlays (live ArcGIS + bundled GeoJSON fallback)
- **`data_source.py`** — Resolves data files from Cloudflare R2 or bundled fallback

### Frontend (`static/src/` TypeScript modules)

Entry: `static/index.html` → `static/src/main.ts` → lazy-loads `app-boot.ts`.

Key modules:
- **`state.ts`** — STATES catalog, deviceToken (localStorage UUID), currentState
- **`map-setup.ts`** — Singleton map instance, base tile providers, basemap persistence
- **`rivers.ts`** — River catalog, viewport-vs-state mode toggle (zoom 9+ = nearby, <9 = selected state), filter predicates
- **`streams.ts`** — Clickable NHDPlus stream network overlay
- **`map-layers.ts`** — Overlay groups (access points, stocked waters, public lands) with lazy-load fetchers
- **`snap-sheet.ts`** — Mobile bottom-sheet drag/swipe/snap
- **`sparkline.ts`** — 1-year USGS gauge trend SVG renderer

Legacy `static/app.js` (~1K lines) still handles state-selector, controls, auth, catches, pins — being migrated to TS modules in PR series B1+.

**Service worker** (`static/sw.js`): network-first for shell assets, stale-while-revalidate for `/api/rivers`, cache-first for icons.

### Caching Layers
1. **L1 (in-process LruTtl)** — USGS medians, gauge metadata. Per-worker, bounded
2. **L2 (Postgres)** — `river_stats`, `gauge_meta`, `river_snapshot`. Cross-restart durable
3. **L3 (Service Worker)** — Shell + `/api/rivers` for instant returning-user paint

### External APIs
- **USGS NWIS** — Instantaneous values (IV) + daily statistics
- **USGS NLDI** — Stream identity lookup (COMID → gnis_name). Retries with exponential backoff on 429/503
- **State ArcGIS REST** — Trout designations (VA DWR, MD DNR), access points
- **NOAA api.weather.gov** — Weather enrichment for catch logs (no API key needed)
- **Resend** — Magic-link auth emails (dev mode prints link to console)
- **Cloudflare R2** — Hosts national NHDPlus VAA, clickable streams, PAD-US data

### Scoring Logic
- **Water temp:** Green 48-65°F, Orange 45-48 or 65-68°F, Red >68 or <40°F
- **Flow (CFS):** Compared to USGS historical median for today's date. Good: 0.5x-2x, Fair: 0.25x-0.5x or 2x-3x, Poor: <0.25x or >3x. Absolute thresholds as fallback
- **Overall river score:** Worst gauge wins

## Key Conventions

- **API JSON:** snake_case (`site_no`, `gnis_name`, `levelpathid`)
- **TypeScript:** camelCase (`getCurrentSt`, `setCurrentSt`)
- **CSS classes:** `bl-` prefix for brand, `.marker--green/yellow/red` for condition discs, design tokens in `static/tokens.css` (`--bl-*`, `--fg-*`, `--bg-surface`)
- **Environment variables:** `DATABASE_URL` (Postgres), `BLUELINER_DB` (SQLite fallback), `DATA_BASE_URL` (R2 base URL), `REFRESH_TOKEN` (ops auth for `/internal/refresh`)

## Deployment

Hosted on **Render** (free tier). GitHub Actions provide:
- `ci.yml` — pytest on push/PR
- `refresh-precompute.yml` — POST `/internal/refresh` every 30 min (needs `BLUELINES_URL` + `REFRESH_TOKEN` secrets)
- `keep-warm.yml` — GET `/healthz` every 10 min to prevent free-tier sleep
- `data-build.yml` — Scheduled NHDPlus/PAD-US rebuilds
- `endpoint-watch.yml` — Scheduled (every 6h) probe of the flaky state-GIS endpoints we're waiting on (`data/watch/watchlist.json` + both `candidates.json`); captures field dumps / discovery / verify verdicts when a server recovers. Step summary = at-a-glance status; full report (`gis_verify_out/WATCH.md`) committed to the long-lived `endpoint-watch` branch. Passing candidates flagged READY TO PROMOTE (never auto-edits `sources.json`)

## Claude Code sandbox limitations (read before network/CI work)

- **Egress allowlist**: only hosts the app calls at runtime are reachable
  (USGS/NOAA, services.dwr.virginia.gov, dnr.geodata.md.gov, PASDA, github.com).
  Most state GIS hosts, all `*.arcgis.com` orgs, `www.arcgis.com` search, and
  WebFetch targets return proxy 403 "Host not in allowlist". Do NOT burn time
  probing endpoints locally -- use the gis-endpoint-verify workflow: put
  `states:`/`ST|url` lines in `scripts/gis_verify_request.txt`, push, and read
  the committed `gis_verify_out/REPORT.txt`.
- **Actions artifacts are NOT downloadable from the sandbox** (Azure blob
  storage host is blocked). To inspect build output, print what you need in
  the workflow log or commit small reports back to the branch.
- **`data-build.yml` publishes to R2 only from `main` AND only with the
  `upload: true` dispatch input** (defaults to false -- a run with just
  `r2_prefix` set builds an artifact and silently skips the Publish step).
  Production data updates require merge first, then a main dispatch with
  BOTH `r2_prefix` and `upload: true`, then a `DATA_BASE_URL` cutover on
  Render to the new prefix.
- **Anonymous GitHub API rate-limits fast** (60/hr shared) -- poll at >=120s
  intervals or use the authenticated GitHub MCP tools (whose list responses
  are huge: extract fields via the saved-output file, never read raw).
