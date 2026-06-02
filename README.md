# Blueliner

A real-time stream condition monitor for fly fishermen.

![Blueliner Demo](blueliner-demo.gif?raw=true)

## The Problem

Fly fishing is deeply condition-dependent. Flow rate, water temperature, and discharge
levels determine whether a river is worth fishing on any given day. Current tools for
checking conditions -- the USGS water data website, scattered fishing forums -- are
fragmented, slow, and not designed for quick decision-making. Blueliner consolidates
live sensor data from USGS monitoring stations into a single, fast, map-based view
so you can check conditions before you drive to the water.

## Features

- **One pin per river** -- gauges are grouped into rivers; a single marker per river opens a panel aggregating overall rating, every gauge's readings, hatches, and nearby stocking (far less map clutter)
- **Shape-coded condition markers** -- moss / ochre / clay / stone discs, each paired with a shape glyph (filled, dotted, line, dashed outline) so the four condition states are distinguishable in monochrome and colorblind-friendly
- **Verdict sentence** -- every river panel opens with one plain-English line built from gauge data: "Flow is 20% below average for this time of year and water temp is ideal."
- **Historical flow context** -- current discharge vs. the historical median for today's date, powered by the USGS Statistics API
- **Trout stream overlay** -- statewide designated trout water (VA DWR / MD DNR, OBJECTID keyset-paginated for full coverage) as a toggleable layer, plus per-river spatial tagging
- **Access points overlay** -- boat ramps, walk-in trails, fishing piers, parking, and wading-access spots as a toggleable layer. Type-coded markers; popup includes access tier (public / permit / fee), agency link, and freeform notes. Bundled baselines for MD / VA / WV / PA, with a documented contributor path to add more states + live state-DNR ArcGIS overlays
- **Swappable base maps** -- Street (CARTO), Satellite (Esri World Imagery), and Topographic (USGS National Map) -- one segmented control in the Layers tab, choice persists across sessions via localStorage
- **Public-lands overlay (PAD-US 4.0)** -- every parcel in USGS GAP's Protected Areas Database, tinted by access tier (Open / Restricted) and click-to-identify (unit name, managing agency, designation, access tier). Renders at zoom 8+; uncolored areas read as "either private or PAD-US doesn't know -- treat as private"
- **Hatch guidance** -- "what's hatching now" per river, resolved to a sub-state hatch zone and the current month
- **Stocking** -- well-known stocked / specially-managed waters (MD/VA/WV baseline + live VA DWR feed) surfaced in the river panel with species/season/agency link
- **1-year flow trend** -- on-demand USGS daily-values sparkline per gauge in the river panel (served live, never stored)
- **National coverage (lower 48)** -- all 50 states + DC via the state selector, with USGS gauges, NHDPlusV2 flowlines (2.69M rows), and clickable streams (742K stream-order 3+) live for the entire lower 48. Trout/stocking/hatch overlays remain mid-Atlantic and expanding.
- **Unified controls panel** -- Layers / Filters / Legend as one tabbed sheet (desktop popover, mobile bottom-sheet with peek/full snap states) opened via three direct-entry buttons in the header
- **Instant filters** -- filter by condition, trout water, hatch, or stocking and switch states client-side, with no full page reload
- **Saved pins** -- drop a pin with a note anywhere on the map; private to your device via an opaque token, or to your account once you sign in
- **Accounts (optional)** -- passwordless magic-link sign-in (no passwords stored); anonymous use is fully supported, and on first sign-in you can claim the pins you saved on that device
- **Catch log** -- signed-in anglers log a catch (species, length, fly, notes) from any river panel; private by default
- **Auto-enrichment** -- each catch automatically captures the conditions at log time: USGS flow (vs. historical median) and water temperature, NOAA air temperature / barometric pressure / sky conditions, moon phase, and the active hatch window -- so patterns ("what produces fish") emerge over a season without manual entry
- **Installable PWA** -- mobile + desktop, with an offline app shell (network-first for HTML/JS/CSS so deploys propagate instantly; stale-while-revalidate for `/api/rivers` so a returning visitor's map paints before the network answers)

## Tech Stack

### Backend
- **FastAPI** -- async JSON API
- **USGS NWIS API** -- real-time stream sensor data (instantaneous values + daily statistics)
- **USGS The National Map** -- labeled rivers/streams as a hydrography tile overlay (no key, national)
- **USGS NLDI + NHDPlusV2** -- river identity and flowline geometry; `LevelPathID` topology keeps a tributary's line from bleeding onto the main stem at a confluence
- **NOAA api.weather.gov** -- air temperature / barometric pressure / sky conditions for catch enrichment (free, no key)
- **State fisheries ArcGIS REST services** -- trout stream designations (VA DWR, MD DNR)
- **Resend** -- transactional email for magic-link sign-in
- **GeoPandas / pyogrio / dbfread / py7zr** -- dev-only data pipeline for `scripts/build_*.py`; trimmed out of the runtime image (saves ~43 MB RSS on the 512 MB Render tier)
- **SQLite / Postgres** -- user-content datastore (accounts, sessions, pins, catch log) **plus** NHDPlus VAA + clickable streams + public lands; SQLite locally, Postgres in production via the same `db.py`. Postgres uses a GiST `box` index for state-scale viewport queries (sub-100 ms on 742 K rows; `init_db()` migrates idempotently)
- **Cloudflare R2** -- hosts the national NHDPlus + PAD-US data files (~80-150 MB total). `data_source.resolve_data_file` downloads them on boot and falls back to bundled mid-Atlantic files when `DATA_BASE_URL` is unset (dev)
- **gunicorn + uvicorn workers** -- production server (Docker, deployable to Render)
- **httpx** -- async HTTP client; NLDI calls retry with exponential backoff + jitter on 429/503 throttling

### Frontend
- **Vite + TypeScript** -- build pipeline introduced in PR #68 (B1a). `static/src/` is the typed module graph, bundled to `static/dist/assets/index-<hash>.{js,css}` with a Vite manifest. Dev server on `:5173` proxies `/api` + `/auth` + `/static` + `/sw.js` to FastAPI on `:8000` so the SPA + the Python backend feel like one origin.
- **Module layout** (`static/src/`):
  - `main.ts` -- Vite entry; imports CSS + the TS modules + the legacy `app.js`
  - `state.ts` -- `STATES` catalog, `deviceToken`, `currentState`
  - `util.ts` -- `esc`, `popupOpts`, `refreshIcons` (Lucide hydration)
  - `sparkline.ts` -- 1-yr USGS gauge trend SVG renderer + hover wiring
  - `map-setup.ts` -- singleton `L.map`, base tile providers, USGS hydro overlay, `bl_basemap` persistence
  - `map-layers.ts` -- trout / access / public-lands GeoJSON layer groups + their lazy-load fetchers + popup HTML helpers
  - `snap-sheet.ts` -- shared bottom-sheet drag + tap + swipe + snap logic
  - `river-panel.ts` -- open/close, highlight state machine, snap-sheet wiring
  - `streams.ts` -- clickable NHDPlus stream network, highlight, and the ungauged-card flow
  - `rivers.ts` -- catalog state, render + filter + condition-icon, viewport vs state mode, river-line fetching
  - `app.js` -- the remaining legacy code: state selector handler, controls panel, auth, catches, saved pins (~1k lines; further extractions tracked as B1i+ in the PR series)
- **Leaflet 1.9** -- map renderer (npm-bundled by Vite; the `static/vendor/leaflet/` directory was retired in PR #72)
- **Lucide** -- outline icon library (CDN script, hydrated via `refreshIcons()` after every dynamic HTML inject)
- **Design system tokens** -- `static/tokens.css` ships the cool-slate / river-blue / copper-accent palette as CSS custom properties; component CSS reads `var(--bl-...)` / `var(--fg-1)` / `var(--bg-surface)`. Shape-coded `.marker--*` discs (color + glyph) live alongside the design-system `.cond` / `.pill` / `.btn` primitives in `static/app.css`.
- **Service worker** (`static/sw.js`) -- NETWORK-FIRST for the shell, STALE-WHILE-REVALIDATE for `/api/rivers`, CACHE-FIRST for `/static/icons/*`. Vite-hashed bundles aren't pre-cached (the names change every deploy) -- network-first picks them up on first navigate and caches for offline reloads. The `CACHE` constant bumps on every shell-affecting deploy so returning browsers force-refetch on next visit.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 20+ (for the frontend build)

### One-time setup

```bash
pip install -r requirements.txt
npm install
```

### Local dev (recommended): Vite + uvicorn side-by-side

Two terminals, full HMR on the frontend:

```bash
# Terminal 1: FastAPI
uvicorn main:app --reload

# Terminal 2: Vite dev server (proxies /api, /auth, /static, /sw.js -> :8000)
npm run dev
```

Browse to `http://localhost:5173`.

### Local dev (single command): production build

If you don't need HMR:

```bash
npm run build           # emits static/dist/
uvicorn main:app --reload
```

Browse to `http://localhost:8000/map`. FastAPI's `/map` route serves `static/dist/index.html` when present, falling back to the source `static/index.html` otherwise.

With no `DATABASE_URL` set, all user content (accounts, sessions, pins, catch log) is stored in a local SQLite file (`blueliner.db`, override with `BLUELINER_DB`). Magic-link email runs in dev mode until `RESEND_API_KEY` is set -- the sign-in link is written to the log so local auth works fully offline.

### Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

Frontend typecheck (validates `static/types.d.ts` contracts + the TS modules under `static/src/`):

```bash
npm run typecheck
```

## Deploying (24/7 on Render)

`render.yaml` is a Render Blueprint that provisions the Docker web service alongside an external Postgres (Neon free tier works; any `postgres://` / `postgresql://` URL works because `db.py` auto-detects from the URL scheme and falls back to SQLite when unset).

The Dockerfile is **multi-stage**: a `node:20-alpine` stage runs `npm ci && npm run build` to produce `static/dist/`, then the `python:3.11-slim` stage copies the built bundle in alongside the FastAPI app. The final image carries no Node runtime.

1. Push to GitHub, then in Render: **New > Blueprint** and pick this repo.
2. Provision a Postgres instance (Render's managed Postgres, Neon's free tier, or anything compatible). In the Render dashboard, set `DATABASE_URL` (declared `sync: false` in `render.yaml`) to its external connection string.
3. Set `DATA_BASE_URL` to the public base URL of the Cloudflare R2 bucket hosting the national NHDPlus + PAD-US data files (see [Data layout](#data-layout-data) below). Without it, the app falls back to the small mid-Atlantic files bundled in git.
4. Health checks hit `/healthz`; HTTPS and a domain are provided by Render.

The free web service sleeps after ~15 min idle -- the keep-warm cron below prevents that.

### Switching Postgres providers

If you move between Postgres hosts (Render → Neon, Neon → managed, etc.), `scripts/migrate_pins.py` ports the only irreplaceable rows (user pins, sessions, catch log); snapshots, stats, NHDPlus VAA, and clickable streams all regenerate from the refresher / first boot within one cycle.

```
OLD_DATABASE_URL=postgres://...old NEW_DATABASE_URL=postgres://...new \
  python scripts/migrate_pins.py
```

Then flip `DATABASE_URL` in Render to the new connection string; the deploy's idempotent `db.init_db()` rebuilds the schema (including the GiST bbox index on `clickable_streams`) and the precompute refresher seeds snapshots within ~45 min.

### Why the map is fast (precompute architecture)

User requests never block on USGS/NLDI/ArcGIS. A background refresher (`precompute.py`) periodically assembles each focused state's rivers (and backfills each gauge's authoritative NHD identity) and persists them to Postgres; `/api/rivers` is then a pure DB read (gzipped, `ETag`/`Cache-Control`, service-worker stale-while-revalidate). Because snapshots live in Postgres they survive a free-tier cold start, so even a just-woken worker paints from the last snapshot instead of a 25s live fetch. Non-focused states are computed lazily on first visit, then persisted.

Two GitHub Actions workflows in `.github/workflows/` close the loop:

- `keep-warm.yml` -- `GET /healthz` every 10 min so the free Render service never sleeps (its 15-min idle threshold is shorter than the refresh cadence).
- `refresh-precompute.yml` -- `POST /internal/refresh` every 30 min, triggering the refresher (single-flight: no-op if a cycle is already running, so the external cron and the in-process loop can't double up).

Required GitHub repo secrets (Settings -> Secrets and variables -> Actions):

| Secret | Value |
|--------|-------|
| `BLUELINES_URL` | Render service URL, e.g. `https://blueliner.app` (no trailing slash). _(Secret name kept as-is to avoid breaking the existing Actions secret; rename to `BLUELINER_URL` only if you also recreate the repo secret.)_ |
| `REFRESH_TOKEN` | The token Render generated for `REFRESH_TOKEN` (read it from the Render dashboard) |

Workflows can also be triggered by hand from the Actions tab (`workflow_dispatch`) to test the wiring.

**Scaling path (config, not rewrite):** Postgres -> Neon free (done; see playbook above). Render web -> Starter (no sleep, raise `WEB_CONCURRENCY`). Put Cloudflare (free) in front to edge-cache the gzipped payloads globally. Promote the in-process refresher to a Render Cron Job (already standalone `precompute.py`).

### Data layout (`data/`)

Per-state trout/stocking/hatch data lives in JSON under `data/`. The contributor guide in [`CONTRIBUTING.md`](CONTRIBUTING.md) covers the schema and the validation step (`python scripts/validate_data.py`).

| Domain | Files | Notes |
|---|---|---|
| Stocking baselines | `data/stocking/<STATE>.json` | Famous + heavily-stocked waters; ~2 km proximity tagging on the map |
| Per-river hatch overrides | `data/hatches/overrides.json` | Curated lists for famous waters (Gunpowder, Penns, Letort, Mossy, Yellow Breeches, Savage, North Branch Potomac) that beat the generic regional zone |
| Trout-stream geometry | `data/trout/<STATE>.json` (optional) | Bundled-GeoJSON fallback when a state agency's live endpoint isn't reliable |
| NHDPlusV2 VAA | `data/nhdplus/vaa.csv.gz` | NHD reach routing attributes (COMID, LevelPathID, gnis_name, ...). The file bundled in git is the **mid-Atlantic dev fallback** (HUC-02 + HUC-05, ~300 K rows, ~3 MB). The **national lower-48 build** (HUC-01 .. HUC-18, ~2.7 M rows, ~30 MB) lives on Cloudflare R2 and is downloaded at startup when `DATA_BASE_URL` is set. Loaded into Postgres once at first boot. Drives the LevelPathID-based flowline filter that keeps a tributary gauge's flowline from extending past the confluence onto the receiving river. Regenerate with `python scripts/build_nhdplus_vaa.py`. |
| NHDPlus clickable streams | `data/nhdplus/clickable_streams.geojson.gz` | Per-stream geometry + trout class for stream-order ≥ 3 reaches and trout-water tributaries. Bundled mid-Atlantic dev fallback (~104 K features, ~6 MB) ↔ national lower-48 (~742 K features, ~49 MB) on R2 via `DATA_BASE_URL`. Loaded into Postgres once; served by viewport via the GiST `box` index. Regenerate with `python scripts/build_clickable_streams.py` (covers all 21 lower-48 NHDPlus VPUs; see [docs/data-build.md](docs/data-build.md) for the build/ship runbook). |
| PAD-US public lands | `data/public_lands/public_lands.geojson.gz` | Every parcel in USGS GAP's Protected Areas Database (federal/state/tribal/local/NGO/private-easement) with canonical attrs (unit_name, manager_type, manager_name, designation, public_access, state_nm). National-only (~80-150 MB gzipped, ~400-700 K features); not bundled in git (~3 GB raw source). Hosted on R2 via `DATA_BASE_URL`; loaded into Postgres once; served by viewport via the same GiST `box` index pattern as clickable streams. Regenerate with `python scripts/build_public_lands.py` (~20-40 min). |

River identity comes from NHDPlusV2's `LevelPathID` (topologically correct, language-agnostic) when the gauge's COMID is in the loaded VAA region, falling back to NHD `gnis_name` via NLDI for COMIDs outside loaded regions, and the USGS station-name heuristic as a last resort.

### Environment variables

| Var | Default | Purpose |
|-----|---------|---------|
| `DATABASE_URL` | _(unset)_ | Postgres URL; absent ⇒ SQLite |
| `BLUELINER_DB` | `./blueliner.db` | SQLite path (when no `DATABASE_URL`) |
| `DATA_BASE_URL` | _(unset)_ | R2 / CDN base URL for `vaa.csv.gz` + `clickable_streams.geojson.gz` + `public_lands.geojson.gz` (versioned prefix, e.g. `https://data.blueliner.app/v1`). Unset ⇒ uses bundled mid-Atlantic files. |
| `WEB_CONCURRENCY` | `1` | gunicorn workers (caches are per-worker) |
| `LOG_LEVEL` | `INFO` | Root log level |
| `PIN_RATE_MAX` | `20` | Max pin creates / IP / minute |
| `REFRESH_INTERVAL` | `2700` | Refresher cadence + snapshot staleness (seconds) |
| `REFRESH_TOKEN` | _(unset)_ | Auth for `POST /internal/refresh` (unset ⇒ 403) |
| `FOCUSED_STATES` | _(built-in)_ | Comma-separated states refreshed every cycle |
| `RESEND_API_KEY` | _(unset)_ | Resend API key for magic-link email. Unset ⇒ dev mode: the sign-in link is logged instead of sent, so local auth works offline. |
| `EMAIL_FROM` | `Blueliner <no-reply@blueliner.app>` | Sender for magic-link email; must be a Resend-verified domain in production |

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
- `GET /api/trout?state=MD` -- designated trout water as GeoJSON (non-blocking; warms in the background)
- `GET /api/access?state=MD` -- access points (ramps / walk-ins / piers / parking / wading) as GeoJSON; bundled baseline + state-DNR live overlay when configured
- `GET /api/public_lands?bbox=west,south,east,north&zoom=10` -- PAD-US parcels overlapping the viewport (zoom-gated; capped at 500 features); pure Postgres read via GiST `bbox &&`
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
- National lower-48 coverage (rivers + flowlines + clickable streams + PAD-US public lands)
- Mobile-responsive layout + installable PWA with snap-sheet panels
- NHDPlusV2 `LevelPathID` river identity (no cross-confluence bleed)
- Optional accounts (magic-link) with anonymous-pin claim
- Private catch log with automatic condition enrichment
- Design system: shape-coded condition markers (color + glyph), token-driven palette, verdict sentence, unified Layers/Filters/Legend controls panel
- Frontend stack modernization: Vite + TypeScript module graph (in-flight; nine modules carved off `app.js`, one PR per domain)

Planned:

- **MapLibre GL JS** -- swap Leaflet for the GL renderer once the TS module split is complete; unlocks vector tiles, GPU-accelerated rendering at the marker counts TroutRoutes-parity will need, and a portable style JSON for an eventual Flutter mobile app
- **Privacy-preserving sharing** -- share a catch at river / watershed / county granularity (never an exact GPS spot), via direct links
- **TroutRoutes-style depth** -- per-segment regulations layered on the existing stream network
- Catch photos and a season summary view

## License

[MIT](LICENSE) © Zion Taber
