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

- **Color-coded condition markers** -- green (good), orange (fair), red (poor), gray (no data) at a glance
- **Historical flow context** -- current discharge compared to the historical median for today's date, powered by the USGS Statistics API
- **Trout stream overlay** -- designated trout water from Virginia DWR and Maryland DNR fisheries data (fully paginated), with spatial tagging of nearby USGS gauges
- **Hatch guidance** -- "what's hatching now" per gauge, resolved to a sub-state hatch zone and the current month
- **Stocking overlay** -- well-known stocked / specially-managed waters (MD/VA/WV baseline + live VA DWR feed) as a toggleable layer and a per-gauge badge
- **1-year flow trend** -- on-demand USGS daily-values sparkline in the gauge popup (served live, never stored)
- **Multi-state support** -- Maryland, Virginia, and West Virginia with a one-click state selector
- **Styled popup cards** -- condition badges, flow trends, data tables, and direct links to USGS site pages
- **Instant filters** -- filter by condition, trout water, hatch, or stocking and switch states client-side, with no full page reload
- **Saved pins** -- drop a pin with a note anywhere on the map; pins persist in a local SQLite store

## Tech Stack

- **FastAPI** -- async JSON API backend
- **USGS NWIS API** -- real-time stream sensor data (instantaneous values + daily statistics)
- **U.S. Census TIGER/Line shapefiles** -- geospatial waterway boundaries
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

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | _(unset)_ | Postgres URL; absent ⇒ SQLite |
| `BLUELINES_DB` | `./bluelines.db` | SQLite path (when no `DATABASE_URL`) |
| `WEB_CONCURRENCY` | `2` | gunicorn workers (caches are per-worker) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `PIN_RATE_MAX` | `20` | Max pin creates / IP / minute |

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
- `GET /streams?state=MD` -- raw live stream data from USGS NWIS for the specified state
- `GET /api/gauges?state=MD` -- scored gauges (conditions, flow context, trout tag, hatch, stocking, popup) as JSON
- `GET /api/waterways?state=MD` -- TIGER waterway geometry as GeoJSON
- `GET /api/trout?state=MD` -- designated trout water as GeoJSON
- `GET /api/stocking?state=MD` -- stocked / specially-managed trout waters as GeoJSON
- `GET /api/history?site_no=01581920` -- ~1 year of USGS daily values (served live, not stored)
- `GET /api/pins` / `POST /api/pins` / `DELETE /api/pins/{id}` -- saved map pins

Supported states: `MD`, `VA`, `WV`, or `all`

## Roadmap

- Mobile-responsive layout for on-the-water use
- "Best bet" recommendation card highlighting the top-scoring station
- Loading skeleton for perceived performance during data fetch
- URL-shareable map state (lat/lon/zoom in the URL)
