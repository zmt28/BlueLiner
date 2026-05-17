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
- **Trout stream overlay** -- designated trout water from Virginia DWR and Maryland DNR fisheries data, with spatial tagging of nearby USGS gauges
- **Multi-state support** -- Maryland, Virginia, and West Virginia with a one-click state selector
- **Styled popup cards** -- condition badges, flow trends, data tables, and direct links to USGS site pages
- **Instant filters** -- filter by condition or trout water and switch states client-side, with no full page reload
- **Saved pins** -- drop a pin with a note anywhere on the map; pins persist in a local SQLite store

## Tech Stack

- **FastAPI** -- async JSON API backend
- **USGS NWIS API** -- real-time stream sensor data (instantaneous values + daily statistics)
- **U.S. Census TIGER/Line shapefiles** -- geospatial waterway boundaries
- **State fisheries ArcGIS REST services** -- trout stream designations (VA DWR, MD DNR)
- **GeoPandas** -- geospatial data processing and spatial joins
- **Leaflet (vendored) + vanilla JS** -- client-side interactive map; no framework, no build step
- **SQLite (stdlib)** -- local datastore for user-generated content (saved pins)
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
- `GET /api/gauges?state=MD` -- scored gauges (conditions, flow context, trout tag, popup) as JSON
- `GET /api/waterways?state=MD` -- TIGER waterway geometry as GeoJSON
- `GET /api/trout?state=MD` -- designated trout water as GeoJSON
- `GET /api/pins` / `POST /api/pins` / `DELETE /api/pins/{id}` -- saved map pins

Supported states: `MD`, `VA`, `WV`, or `all`

## Roadmap

- Mobile-responsive layout for on-the-water use
- "Best bet" recommendation card highlighting the top-scoring station
- Loading skeleton for perceived performance during data fetch
- URL-shareable map state (lat/lon/zoom in the URL)
