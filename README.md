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
- **Access points overlay** -- boat ramps, walk-in trails, fishing piers, parking, and wading-access spots as a toggleable layer. Type-coded markers; popup includes access tier (public / permit / fee), agency link, and freeform notes. Bundled baselines for MD / VA / WV / PA, with a documented contributor path to add more states + live state-DNR ArcGIS overlays
- **Swappable base maps** -- Street (CARTO), Satellite (Esri World Imagery), and Topographic (USGS National Map) -- one segmented control in the filters popover, choice persists across sessions via localStorage
- **Hatch guidance** -- "what's hatching now" per river, resolved to a sub-state hatch zone and the current month
- **Stocking** -- well-known stocked / specially-managed waters (MD/VA/WV baseline + live VA DWR feed) surfaced in the river popup with species/season/agency link
- **1-year flow trend** -- on-demand USGS daily-values sparkline per gauge in the river popup (served live, never stored)
- **National coverage (lower 48)** -- all 50 states + DC via the state selector, with USGS gauges, NHDPlusV2 flowlines (2.69M rows), and clickable streams (742K stream-order 3+) live for the entire lower 48. Trout/stocking/hatch overlays remain mid-Atlantic and expanding.
- **Styled popup cards** -- condition badges, flow trends, data tables, and direct links to USGS site pages
- **Instant filters** -- filter by condition, trout water, hatch, or stocking and switch states client-side, with no full page reload
- **Saved pins** -- drop a pin with a note anywhere on the map; private to your device via an opaque token, or to your account once you sign in
- **Accounts (optional)** -- passwordless magic-link sign-in (no passwords stored); anonymous use is fully supported, and on first sign-in you can claim the pins you saved on that device
- **Catch log** -- signed-in anglers log a catch (species, length, fly, notes) from any river popup; private by default
- **Auto-enrichment** -- each catch automatically captures the conditions at log time: USGS flow (vs. historical median) and water temperature, NOAA air temperature / barometric pressure / sky conditions, moon phase, and the active hatch window -- so patterns ("what produces fish") emerge over a season without manual entry

## Tech Stack

- **FastAPI** -- async JSON API backend
- **USGS NWIS API** -- real-time stream sensor data (instantaneous values + daily statistics)
- **USGS The National Map** -- labeled rivers/streams as a hydrography tile overlay (no key, national)
- **USGS NLDI + NHDPlusV2** -- river identity and flowline geometry; `LevelPathID` topology keeps a tributary's line from bleeding onto the main stem at a confluence
- **NOAA api.weather.gov** -- air temperature / barometric pressure / sky conditions for catch enrichment (free, no key)
- **State fisheries ArcGIS REST services** -- trout stream designations (VA DWR, MD DNR)
- **Resend** -- transactional email for magic-link sign-in
- **GeoPandas / pyogrio / dbfread / py7zr** -- dev-only data pipeline for `scripts/build_*.py`; trimmed out of the runtime image (saves ~43 MB RSS on the 512 MB Render tier)
- **Leaflet (vendored) + vanilla JS** -- client-side interactive map; no framework, no build step
- **SQLite / Postgres** -- user-content datastore (accounts, sessions, pins, catch log) **plus** NHDPlus VAA + clickable streams; SQLite locally, Postgres in production via the same `db.py`. Postgres uses a GiST `box` index for state-scale viewport queries (sub-100 ms on 742 K rows; `init_db()` migrates idempotently)
- **Cloudflare R2** -- hosts the national NHDPlus data files (`vaa.csv.gz`, `clickable_streams.geojson.gz`, ~80 MB total). `data_source.resolve_data_file` downloads them on boot and falls back to bundled mid-Atlantic files when `DATA_BASE_URL` is unset (dev)
- **gunicorn + uvicorn workers** -- production server (Docker, deployable to Render)
- **httpx** -- async HTTP client; NLDI calls retry with exponential backoff + jitter on 429/503 throttling

## Getting Started

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

With no `DATABASE_URL` set, all user content (accounts, sessions, pins,
catch log) is stored in a local SQLite file (`blueliner.db`, override
with `BLUELINER_DB`). Magic-link email runs in dev mode until
`RESEND_API_KEY` is set -- the sign-in link is written to the log so
local auth works fully offline.

Run the tests with:

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Deploying (24/7 on Render)

`render.yaml` is a Render Blueprint that provisions the Docker web
service alongside an external Postgres (Render-managed today; any
`postgres://` / `postgresql://` URL works because `db.py` auto-detects
from the URL scheme and falls back to SQLite when unset).

1. Push to GitHub, then in Render: **New > Blueprint** and pick this repo.
2. Provision a Postgres instance (Render's managed Postgres, Neon's
   free tier, or anything compatible). In the Render dashboard, set
   `DATABASE_URL` (declared `sync: false` in `render.yaml`) to its
   external connection string.
3. Set `DATA_BASE_URL` to the public base URL of the Cloudflare R2
   bucket hosting the national NHDPlus data files (see
   [Data layout](#data-layout-data) below). Without it, the app falls
   back to the small mid-Atlantic files bundled in git.
4. Health checks hit `/healthz`; HTTPS and a domain are provided by Render.

The free web service sleeps after ~15 min idle -- the keep-warm cron
below prevents that.

### Switching Postgres providers

If you move between Postgres hosts (Render → Neon, Neon → managed,
etc.), `scripts/migrate_pins.py` ports the only irreplaceable rows
(user pins, sessions, catch log); snapshots, stats, NHDPlus VAA, and
clickable streams all regenerate from the refresher / first boot
within one cycle.

```
OLD_DATABASE_URL=postgres://...old NEW_DATABASE_URL=postgres://...new \
  python scripts/migrate_pins.py
```

Then flip `DATABASE_URL` in Render to the new connection string; the
deploy's idempotent `db.init_db()` rebuilds the schema (including the
GiST bbox index on `clickable_streams`) and the precompute refresher
seeds snapshots within ~45 min.

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
| NHDPlusV2 VAA | `data/nhdplus/vaa.csv.gz` | NHD reach routing attributes (COMID, LevelPathID, gnis_name, ...). The file bundled in git is the **mid-Atlantic dev fallback** (HUC-02 + HUC-05, ~300 K rows, ~3 MB). The **national lower-48 build** (HUC-01 .. HUC-18, ~2.7 M rows, ~30 MB) lives on Cloudflare R2 and is downloaded at startup when `DATA_BASE_URL` is set. Loaded into Postgres once at first boot. Drives the LevelPathID-based flowline filter that keeps a tributary gauge's flowline from extending past the confluence onto the receiving river. Regenerate with `python scripts/build_nhdplus_vaa.py`. |
| NHDPlus clickable streams | `data/nhdplus/clickable_streams.geojson.gz` | Per-stream geometry + trout class for stream-order ≥ 3 reaches and trout-water tributaries. Bundled mid-Atlantic dev fallback (~104 K features, ~6 MB) ↔ national lower-48 (~742 K features, ~49 MB) on R2 via `DATA_BASE_URL`. Loaded into Postgres once; served by viewport via the GiST `box` index. Regenerate with `python scripts/build_clickable_streams.py`. |

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
| `DATA_BASE_URL` | _(unset)_ | R2 / CDN base URL for `vaa.csv.gz` + `clickable_streams.geojson.gz` (versioned prefix, e.g. `https://data.blueliner.app/v1`). Unset ⇒ uses bundled mid-Atlantic files. |
| `WEB_CONCURRENCY` | `1` | gunicorn workers (caches are per-worker) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `PIN_RATE_MAX` | `20` | Max pin creates / IP / minute |
| `REFRESH_INTERVAL` | `2700` | Refresher cadence + snapshot staleness (seconds) |
| `REFRESH_TOKEN` | _(unset)_ | Auth for `POST /internal/refresh` (unset ⇒ 403) |
| `FOCUSED_STATES` | _(built-in)_ | Comma-separated states refreshed every cycle |
| `RESEND_API_KEY` | _(unset)_ | Resend API key for magic-link email. Unset ⇒ dev mode: the sign-in link is logged instead of sent, so local auth works offline. |
| `EMAIL_FROM` | `Blueliner <no-reply@blueliner.app>` | Sender for magic-link email; must be a Resend-verified domain in production |

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

**App shell & map data**
- `GET /` -- redirects to the Maryland map
- `GET /map` -- the application shell (static client; state/filters resolved in the browser)
- `GET /healthz` -- liveness check (used by the keep-warm cron)
- `GET /api/states` -- supported states (code, name, map center); drives the selector
- `GET /streams?state=MD` -- raw live stream data from USGS NWIS for the specified state
- `GET /api/rivers?state=MD` -- gauges grouped into rivers (rating, hatch, stocking, aggregated popup) as JSON
- `GET /api/river_lines?state=MD` (or `?bbox=`) -- river flowline geometry as GeoJSON
- `GET /api/river_geom?site_no=01581920` -- a single river's flowline geometry
- `GET /api/trout?state=MD` -- designated trout water as GeoJSON (non-blocking; warms in the background)
- `GET /api/access?state=MD` -- access points (ramps / walk-ins / piers / parking / wading) as GeoJSON; bundled baseline + state-DNR live overlay when configured
- `GET /api/history?site_no=01581920` -- ~1 year of USGS daily values (served live, not stored)

**Accounts (magic-link)**
- `POST /api/auth/request-link` -- email a sign-in link (rate-limited; always 204, no account enumeration)
- `GET /auth/consume?token=...` -- redeem a link, set the session cookie, redirect home
- `POST /api/auth/logout` -- end the session
- `GET` / `PATCH` / `DELETE /api/me` -- current user; update display name; delete account

**Pins**
- `GET /api/pins` / `POST /api/pins` / `DELETE /api/pins/{id}` -- saved map pins
- `GET /api/pins/claimable` / `POST /api/pins/claim` -- list / claim anonymous device pins after sign-in

**Catch log**
- `GET` / `POST /api/catches` -- list (with `species` / date filters) / create a catch
- `GET` / `PATCH` / `DELETE /api/catches/{id}` -- read / edit / delete a catch
- `GET /api/catches/enrichment-preview` -- live conditions snapshot for the catch form

**Ops**
- `POST /internal/refresh` -- trigger the precompute refresher (requires `X-Refresh-Token`)

Supported states: any U.S. state two-letter code (plus `DC`); see `GET /api/states`

## Roadmap

Shipped:

- Real-time conditions, scoring, hatches, stocking, trout overlay
- Mobile-responsive layout + installable PWA
- NHDPlusV2 `LevelPathID` river identity (no cross-confluence bleed)
- Optional accounts (magic-link) with anonymous-pin claim
- Private catch log with automatic condition enrichment

Planned:

- **Privacy-preserving sharing** -- share a catch at river / watershed / county
  granularity (never an exact GPS spot), via direct links
- **TroutRoutes-style depth** -- access points, per-segment regulations, and
  public-land boundaries layered on the existing stream network
- Catch photos and a season summary view

## License

[MIT](LICENSE) © Zion Taber
