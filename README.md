# BlueLines

A real-time stream condition monitor for fly fishermen.

## The Problem

Fly fishing is deeply condition-dependent. Flow rate, water temperature, and discharge
levels determine whether a river is worth fishing on any given day. Current tools for
checking conditions -- the USGS water data website, scattered fishing forums -- are
fragmented, slow, and not designed for quick decision-making. BlueLines consolidates
live sensor data from USGS monitoring stations into a single, fast, map-based view
so you can check conditions before you drive to the water.

<!-- TODO: add screenshot -->

## Tech Stack

- **FastAPI** -- async API backend
- **USGS National Water Information System API** -- real-time stream sensor data
- **U.S. Census TIGER/Line shapefiles** -- geospatial waterway boundaries
- **GeoPandas** -- geospatial data processing
- **Folium + Branca** -- interactive map rendering
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

Then open: `http://localhost:8000/map`

## API Endpoints

- `GET /streams?state=MD` -- fetches live stream data from USGS NWIS for all active
  monitoring sites (stream gauges, springs, wastewater treatment plants) in the
  specified state. Supports MD, VA, and WV.
- `GET /map?state=MD` -- generates and returns an interactive HTML map with waterway
  geometries, live sensor markers, and fishing condition scores.
  Omit the state parameter to load all supported states.

## Fishing Conditions Scoring

Each monitoring station is scored based on current readings and displayed with
color-coded markers:

- **Green** -- good conditions. Water temperature and flow are in the sweet spot
  for active fish.
- **Orange** -- fair conditions. Temperature or flow is outside the ideal range
  but still fishable.
- **Red** -- poor conditions. Water is too warm, too cold, or flow is too high/low
  for a productive trip.
- **Gray** -- insufficient data to score this station.

### How Scoring Works

**Water temperature** (optimized for trout):
- Green: 48-65 degrees F
- Yellow: 45-48 or 65-68 degrees F
- Red: above 68 or below 40 degrees F

**Flow rate** (discharge in cubic feet per second):
- Scored relative to each site's typical range using available data.
  Extremely high or low flows are flagged.

## Roadmap

- Mobile-responsive layout for on-the-water use
- Species filter (show only trout streams, bass streams, etc.)
- Historical conditions charting per station
- User-configurable alert thresholds
