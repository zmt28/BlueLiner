# Bundled trout-stream geometry

This directory holds optional per-state GeoJSON files for trout-stream
layers where a live ArcGIS source is unavailable or unreliable. Loaded
by `trout.py` as a fallback when no live source is configured for a
state.

Each file is a single GeoJSON `FeatureCollection` in EPSG:4326. Only
the geometry is used (matching the in-memory representation produced by
`trout._slim`). Attributes are ignored.

To add a state:

1. Convert the state agency's published shapefile or GeoJSON to
   EPSG:4326 (`ogr2ogr -f GeoJSON -t_srs EPSG:4326 out.json in.shp`).
2. Simplify aggressively (`mapshaper -simplify weighted 5%`) -- a few
   MB at most, smaller is better.
3. Save as `data/trout/<STATE>.json`.
4. Run `python scripts/validate_data.py` to confirm it loads.
5. Add the state to `trout.py:BUNDLED_TROUT_STATES` so the loader picks
   it up.

WV is the first candidate (the live WV DEP/DNR endpoint isn't reliably
reachable). PA + NY may follow if their ArcGIS layers prove flaky.
