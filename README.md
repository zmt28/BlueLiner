# Blueliner

A real-time stream condition monitor for fly fishermen.

![Blueliner Demo](blueliner-demo.gif)

## The Problem

Fly fishing is deeply condition-dependent. Flow rate, water temperature, and discharge
levels determine whether a river is worth fishing on any given day. Current tools for
checking conditions -- the USGS water data website, scattered fishing forums -- are
fragmented, slow, and not designed for quick decision-making. Blueliner consolidates
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

Blueliner was built using Claude Code as a development accelerator. Every line of code
was written and reviewed by hand -- Claude Code was used to navigate unfamiliar APIs,
debug geospatial data processing, and iterate faster. The result is code I understand
and own completely.

## Getting Started

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

With no `DATABASE_URL` set, pins are stored in a local SQLite file
(`blueliner.db`, override with `BLUELINER_DB`).

## Deploying (24/7 on Render)

`render.yaml` is a Render Blueprint that provisions the Docker web
service. **Postgres is external** (Neon free tier, persistent --
Render's free Postgres expires at ~90 days and the whole instant-load
architecture leans on the DB):

1. Push to GitHub, then in Render: **New > Blueprint** and pick this repo.
2. In the Render dashboard, set `DATABASE_URL` (declared as
   `sync: false` in `render.yaml`) to your Neon connection string -- see
   the [Postgres cutover playbook](#postgres-cutover-playbook-neon).
   `db.py` selects Postgres automatically for any `postgres://` /
   `postgresql://` URL; SQLite otherwise. No code change to switch.
3. Health checks hit `/healthz`; HTTPS and a domain are provided by Render.

The free web service sleeps after ~15 min idle -- the keep-warm cron
below prevents that.

### Postgres cutover playbook (Neon)

One-time migration off Render's expiring free Postgres onto Neon's
persistent free tier. The script copies the only irreplaceable data
(user pins) plus immutable flowline geometry; snapshots and stats
regenerate from the refresher within one cycle.

1. **Provision Neon.** Sign up (free, no card), create a project, pick
   a region close to Render's (Render free runs in Oregon -- choose
   `us-west-2` if available). Copy the **direct** connection string
   (not the pooler URL); it looks like
   `postgresql://USER:PW@ep-xxx.aws.neon.tech/DBNAME?sslmode=require`.
2. **Migrate.** Locally, with the current Render free DB URL in
   `OLD_DATABASE_URL` (read from the Render dashboard) and the Neon URL
   in `NEW_DATABASE_URL`:
   ```
   OLD_DATABASE_URL=... NEW_DATABASE_URL=... python scripts/migrate_pins.py
   ```
   Confirm the printed row counts.
3. **Flip the env var.** In Render dashboard → service → Environment,
   set `DATABASE_URL` to the Neon URL. Render redeploys; `lifespan`'s
   idempotent `db.init_db()` is a no-op since the script already
   created the tables.
4. **Seed snapshots on Neon.** Trigger the `refresh-precompute`
   workflow once by hand from the Actions tab.
5. **Smoke-test.** `/healthz` 200; `/api/pins` lists your migrated
   pins for your usual device; `/api/rivers?state=MD` returns a
   non-empty list; `/api/river_lines?state=MD` returns features.
6. **Wait ~1 week**, then deprovision the old Render Postgres in the
   dashboard (rollback window if anything's wrong).

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
| `BLUELINES_URL` | Render service URL, e.g. `https://blueliner.app` (no trailing slash). _(Secret name kept as-is to avoid breaking the existing Actions secret; rename to `BLUELINER_URL` only if you also recreate the repo secret.)_ |
| `REFRESH_TOKEN` | The token Render generated for `REFRESH_TOKEN` (read it from the Render dashboard) |

Workflows can also be triggered by hand from the Actions tab
(`workflow_dispatch`) to test the wiring.

**Scaling path (config, not rewrite):** Postgres -> Neon free (done; see
playbook above). Render web -> Starter (no sleep, raise
`WEB_CONCURRENCY`). Put Cloudflare (free) in front to edge-cache the
gzipped payloads globally. Promote the in-process refresher to a Render
Cron Job (already standalone `precompute.py`).

### Data layout (`data/`)

Per-state trout/stocking/hatch data lives in JSON under `data/`. The
contributor guide in [`CONTRIBUTING.md`](CONTRIBUTING.md) covers the
schema and the validation step (`python scripts/validate_data.py`).

| Domain | Files | Notes |
|---|---|---|
| Stocking baselines | `data/stocking/<STATE>.json` | Famous + heavily-stocked waters; ~2 km proximity tagging on the map |
| Per-river hatch overrides | `data/hatches/overrides.json` | Curated lists for famous waters (Gunpowder, Penns, Letort, Mossy, Yellow Breeches, Savage, North Branch Potomac) that beat the generic regional zone |
| Trout-stream geometry | `data/trout/<STATE>.json` (optional) | Bundled-GeoJSON fallback when a state agency's live endpoint isn't reliable |
| NHDPlusV2 VAA | `data/nhdplus/vaa.csv.gz` | ~300K NHD reach routing attributes (COMID, LevelPathID, gnis_name, ...) for HUC-02 + HUC-05 (mid-Atlantic). Loaded into Postgres once at first boot. Drives the LevelPathID-based flowline filter that keeps a tributary gauge's flowline from extending past the confluence onto the receiving river. Regenerate with `python scripts/build_nhdplus_vaa.py`. |

River identity comes from NHDPlusV2's `LevelPathID` (topologically
correct, language-agnostic) when the gauge's COMID is in the loaded
VAA region, falling back to NHD `gnis_name` via NLDI for COMIDs
outside loaded regions, and the USGS station-name heuristic as a last
resort.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | _(unset)_ | Postgres URL; absent ⇒ SQLite |
| `BLUELINER_DB` | `./blueliner.db` | SQLite path (when no `DATABASE_URL`) |
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
