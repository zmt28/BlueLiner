# BlueLines

A real-time stream condition monitor for fly fishermen.

![BlueLines Demo](bluelines-demo.gif)

## The Problem

Fly fishing is deeply condition-dependent. Flow rate, water temperature, and discharge
levels determine whether a river is worth fishing on any given day. Current tools for
checking conditions -- the USGS water data website, scattered fishing forums -- are
fragmented, slow, and not designed for quick decision-making. BlueLines consolidates
live sensor data from USGS monitoring stations into a single, fast, map-based view
so you can check conditions before you drive to the water.

## Features

- **One pin per river** -- gauges are grouped into rivers; a single marker per river opens a popup aggregating overall rating, every gauge's readings, hatches, and nearby stocking (far less map clutter)
- **Color-coded markers** -- green (good), orange (fair), red (poor), gray (no data) at a glance
- **Historical flow context** -- current discharge vs. the historical median for today's date, powered by the USGS Statistics API
- **Trout stream overlay** -- statewide designated trout water (VA DWR / MD DNR, OBJECTID keyset-paginated for full coverage) as a toggleable layer, plus per-river spatial tagging
- **Hatch guidance** -- "what's hatching now" per river, resolved to a sub-state hatch zone and the current month
- **Stocking** -- well-known stocked / specially-managed waters (MD/VA/WV baseline + live VA DWR feed) surfaced in the river popup with species/season/agency link
- **1-year flow trend** -- on-demand USGS daily-values sparkline per gauge in the river popup (served live, never stored)
- **National gauge coverage** -- all 50 states + DC via the state selector (conditions/trend are national; trout/stocking/hatch data are mid-Atlantic and expanding)
- **Styled popup cards** -- condition badges, flow trends, data tables, and direct links to USGS site pages
- **Instant filters** -- filter by condition, trout water, hatch, or stocking and switch states client-side, with no full page reload
- **Saved pins** -- drop a pin with a note anywhere on the map; pins are private to your device via an opaque token (no login), persisted in SQLite/Postgres

## Tech Stack

- **FastAPI** -- async JSON API backend
- **USGS NWIS API** -- real-time stream sensor data (instantaneous values + daily statistics)
- **USGS The National Map** -- labeled rivers/streams as a hydrography tile overlay (no key, national)
- **State fisheries ArcGIS REST services** -- trout stream designations (VA DWR, MD DNR)
- **GeoPandas** -- geospatial data processing and spatial joins
- **Leaflet (vendored) + vanilla JS** -- client-side interactive map; no framework, no build step
- **SQLite / Postgres** -- user-content datastore (saved pins); SQLite locally, Postgres in production via the same `db.py`
- **gunicorn + uvicorn workers** -- production server (Docker, deployable to Render)
- **httpx** -- async HTTP client

## Built with AI

BlueLines was built using Claude Code as a development accelerator. Every line of code
was written and reviewed by hand -- Claude Code was used to navigate unfamiliar APIs,
debug geospatial data processing, and iterate faster. The result is code I understand
and own completely.

## Getting Started

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

With no `DATABASE_URL` set, pins are stored in a local SQLite file
(`bluelines.db`, override with `BLUELINES_DB`).

## Deploying (24/7 on Render)

`render.yaml` is a Render Blueprint that provisions the Docker web service
plus a managed Postgres database and wires them together:

1. Push to GitHub, then in Render: **New > Blueprint** and pick this repo.
2. Render builds the `Dockerfile`, creates `bluelines-db`, and injects
   `DATABASE_URL` -- `db.py` automatically uses Postgres when it's a
   `postgres://` URL, SQLite otherwise. No code change to switch.
3. Health checks hit `/healthz`; HTTPS and a domain are provided by Render.

Plans default to `free` (Render's free Postgres expires and free web
services sleep when idle) -- bump both to a paid tier for genuine 24/7.

### Why the map is fast (precompute architecture)

User requests never block on USGS/NLDI/ArcGIS. A background refresher
(`precompute.py`) periodically assembles each focused state's rivers and
flowline geometry and persists them to Postgres; `/api/rivers` and
`/api/river_lines` are then pure DB reads (gzipped, `ETag`/`Cache-Control`,
service-worker stale-while-revalidate). Because snapshots live in
Postgres they survive a free-tier cold start, so even a just-woken worker
paints from the last snapshot instead of a 25s live fetch. Non-focused
states are computed lazily on first visit, then persisted.

Two GitHub Actions workflows in `.github/workflows/` close the loop:

- `keep-warm.yml` -- `GET /healthz` every 10 min so the free Render
  service never sleeps (its 15-min idle threshold is shorter than the
  refresh cadence).
- `refresh-precompute.yml` -- `POST /internal/refresh` every 30 min,
  triggering the refresher (single-flight: no-op if a cycle is already
  running, so the external cron and the in-process loop can't double up).

Required GitHub repo secrets (Settings -> Secrets and variables -> Actions):

| Secret | Value |
|--------|-------|
| `BLUELINES_URL` | Render service URL, e.g. `https://bluelines.onrender.com` (no trailing slash) |
| `REFRESH_TOKEN` | The token Render generated for `REFRESH_TOKEN` (read it from the Render dashboard) |

Workflows can also be triggered by hand from the Actions tab
(`workflow_dispatch`) to test the wiring.

**Scaling path (config, not rewrite):** Render web -> Starter (no sleep,
raise `WEB_CONCURRENCY`); **Render Postgres -> a persistent paid plan**
(the free DB expires at ~90 days and the whole design leans on it --
the most important upgrade); put Cloudflare (free) in front to edge-cache
the gzipped payloads globally; promote the in-process refresher to a
Render Cron Job (it's already standalone `precompute.py`).

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | _(unset)_ | Postgres URL; absent ⇒ SQLite |
| `BLUELINES_DB` | `./bluelines.db` | SQLite path (when no `DATABASE_URL`) |
| `WEB_CONCURRENCY` | `1` | gunicorn workers (caches are per-worker) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `PIN_RATE_MAX` | `20` | Max pin creates / IP / minute |
| `REFRESH_INTERVAL` | `2700` | Refresher cadence + snapshot staleness (seconds) |
| `REFRESH_TOKEN` | _(unset)_ | Auth for `POST /internal/refresh` (unset ⇒ 403) |
| `FOCUSED_STATES` | _(built-in)_ | Comma-separated states refreshed every cycle |

Then open: `http://localhost:8000`

## How Scoring Works

Each monitoring station is scored based on current readings:

**Water temperature** (optimized for trout):
- Green: 48-65 degrees F
- Orange: 45-48 or 65-68 degrees F
- Red: above 68 or below 40 degrees F

**Flow rate** (discharge in cubic feet per second):
- Scored relative to the site's historical median for today's date
- Good: 0.5x to 2x of median
- Fair: 0.25x-0.5x or 2x-3x of median
- Poor: below 0.25x or above 3x of median
- Falls back to absolute thresholds when no historical data is available

## API Endpoints

- `GET /` -- redirects to the Maryland map
- `GET /map` -- the application shell (static client; state/filters resolved in the browser)
- `GET /api/states` -- supported states (code, name, map center); drives the selector
- `GET /streams?state=MD` -- raw live stream data from USGS NWIS for the specified state
- `GET /api/rivers?state=MD` -- gauges grouped into rivers (rating, hatch, stocking, aggregated popup) as JSON
- `GET /api/trout?state=MD` -- designated trout water as GeoJSON (non-blocking; warms in the background)
- `GET /api/history?site_no=01581920` -- ~1 year of USGS daily values (served live, not stored)
- `GET /api/pins` / `POST /api/pins` / `DELETE /api/pins/{id}` -- saved map pins

Supported states: any U.S. state two-letter code (plus `DC`); see `GET /api/states`

## Roadmap

- Mobile-responsive layout for on-the-water use
- "Best bet" recommendation card highlighting the top-scoring station
- Loading skeleton for perceived performance during data fetch
- URL-shareable map state (lat/lon/zoom in the URL)
