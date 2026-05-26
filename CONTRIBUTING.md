# Contributing data

The bundled trout / stocking / hatch data lives under `data/`. Each file
is plain JSON so edits show up cleanly in PR diffs. After any edit, run
`python scripts/validate_data.py` -- it lints the schema and confirms
coordinates fall inside the right state's bounding box.

## Stocking points (`data/stocking/<STATE>.json`)

Top level: a JSON array. Each entry:

```json
{
  "water": "Gunpowder Falls (Falls Rd / Masemore)",
  "lat": 39.6361,
  "lon": -76.6889,
  "species": ["Brown", "Rainbow"],
  "category": "Tailwater - wild + stocked",
  "season_months": [1, 12],
  "agency_url": "https://dnr.maryland.gov/fisheries/pages/trout/stocking.aspx"
}
```

Notes:

- `season_months` is `[start, end]` with months as integers 1..12. Use
  `[1, 12]` for year-round; ranges that wrap the year are allowed
  (e.g. `[10, 5]` for October through May -- delayed-harvest waters).
- A long river is best represented by **multiple points along the
  reach** -- the proximity tag uses a ~2 km buffer and a single point
  doesn't cover a 20-mile tailwater.
- Coordinates can be approximate access points; precision below ~100 m
  doesn't matter.
- Use the state agency's authoritative URL so the popup links to the
  current stocking schedule.

## Hatch overrides (`data/hatches/overrides.json`)

Keys are **lowercased river names** matched against the NHD `gnis_name`
(or the station-name heuristic when GNIS isn't cached yet). When a key
matches, the entry's chart **replaces** the geographic zone's chart for
that river. Use this for famous waters whose hatches diverge from the
surrounding region (tailwaters, spring creeks, anything where the
zone's general chart is too generic).

Each chart entry:

```json
{
  "insect": "Ephemerella invaria",
  "common_name": "Sulphur",
  "months": [5, 7],
  "peak": [5, 6],
  "hook_sizes": "14-18",
  "time_of_day": "Evening spinner fall",
  "patterns": ["Sulphur Comparadun", "Sulphur Spinner"]
}
```

The leading `_comment` key is reserved and ignored by the loader.

Verify timing/sizes against authoritative regional sources (state TU
chapter pages, university extension guides, local fly-shop hatch
charts) before adding -- the goal is curation that beats the generic
zone, not guesswork.

## Trout-stream geometry (`data/trout/<STATE>.json`)

Optional. A single GeoJSON `FeatureCollection` in EPSG:4326 used as a
fallback when no live state agency endpoint is available. See
`data/trout/README.md` for the workflow. Add a state to
`trout.BUNDLED_TROUT_STATES` (TODO) once a file is in place so the
loader picks it up.

## NHDPlusV2 VAA (`data/nhdplus/vaa.csv.gz`)

This is the routing-attribute table that drives the LevelPathID-based
flowline filter -- it's what stops a tributary gauge's clickable river
from extending past the confluence onto the receiving river. Bundled
in the repo; loaded into Postgres once at first boot.

Don't hand-edit. Regenerate by running:

```sh
pip install dbfread py7zr httpx       # dev-only deps
python scripts/build_nhdplus_vaa.py
```

The script downloads NHDPlusV2 `NHDPlusAttributes` + `NHDSnapshot`
archives for the configured regions (HUC-02 + HUC-05 today), extracts
`PlusFlowlineVAA.dbf` and `NHDFlowline.dbf`, joins on ComID, and
writes the gzipped CSV. Re-run only when expanding coverage to new
regions -- the data itself is frozen at NHDPlusV2's release.

To add a region, edit `REGIONS` at the top of
`scripts/build_nhdplus_vaa.py` (entries are `(id, label, vaa_url,
snap_url)`), rerun the script, and commit the new CSV.

## Clickable-streams network (`data/nhdplus/clickable_streams.geojson.gz`)

The geometry layer of fishing-relevant streams ("bluelining" network).
A reach is clickable if any of: StreamOrder >= 3; state-designated
trout water (any order); order >= 3 tributary of trout water; named
river order >= 5. Each feature carries `comid`, `levelpathid`,
`gnis_name`, `streamorder`, `lengthkm`, `trout_class`. Grouped by
`levelpathid` at runtime so a whole named river is one clickable unit.
~104K flowlines / ~6 MB gzipped for HUC-02 + HUC-05.

`trout_class` values: `wild_reproduction` (PA PASDA "With Tributaries"
+ VA DWR), `class_a` (PA), `wilderness` (PA), `stocked` (PA),
`designated` (MD DNR), or `null` when no trout designation applies.

Don't hand-edit. Regenerate by running:

```sh
pip install dbfread py7zr httpx geopandas shapely   # dev-only deps
python scripts/build_clickable_streams.py
```

The script downloads NHDPlusV2 flowline geometry + routing attributes
(Hydroseq / DnHydroseq / StreamOrder / LevelPathID), fetches state
trout-stream GIS from PA PASDA, VA DWR, and MD DNR, spatial-joins
trout polylines to NHD COMIDs, computes upstream tributaries via the
NHDPlus topology graph, and writes the gzipped GeoJSON. A PA
wild-trout validation report prints at the end.

### Hosting the data files externally (national scale)

The bundled `vaa.csv.gz` + `clickable_streams.geojson.gz` in
`data/nhdplus/` are the mid-Atlantic dev fallback (~9 MB; kept in git
so a fresh clone + `pytest` + `uvicorn main:app` works offline).
National lower-48 versions of those same files (~60-120 MB each) live
on Cloudflare R2 and are pulled at startup via
`data_source.resolve_data_file`.

Hosting convention -- versioned prefix:

```
https://data.blueliner.app/v1/vaa.csv.gz
https://data.blueliner.app/v1/clickable_streams.geojson.gz
```

The `/v1/` segment is what we bump on each data refresh. The
worker-local download cache lives in `/tmp/blueliner-data` and is
keyed by filename, so changing the version segment in `DATA_BASE_URL`
forces a fresh download without a manual purge of any CDN or worker
disk.

To roll new data:

1. Regenerate via `scripts/build_nhdplus_vaa.py` +
   `scripts/build_clickable_streams.py` (extend the `REGIONS` list to
   cover all HUC-2 regions in the lower-48; see the EPA
   NHDPlusV21 S3 bucket index for the per-region archive names and
   vintage suffixes).
2. Upload to R2 under a new version prefix (e.g. `v2/`).
3. Update `DATA_BASE_URL` in the Render dashboard and redeploy.

`.dockerignore` excludes `data/nhdplus/*.gz` from the production image
so `resolve_data_file` always falls through to R2 in prod; dev
unaffected. Unset `DATA_BASE_URL` to roll back -- the app uses
whatever's bundled locally.

### Postgres GiST bbox index (one-time migration)

`init_db()` adds a `bbox box GENERATED STORED` column on
`clickable_streams` + a GiST index on it (Postgres only -- SQLite stays
on the 4-range b-tree path). Fresh deploys get this automatically. For
**existing Postgres installs that pre-date the GiST migration**, the
`ALTER TABLE ADD COLUMN` synchronously backfills 742K+ rows on first
boot, which on a free-tier Postgres takes 3-5 min and blocks startup.
To skip the startup penalty, run the migration manually in TablePlus
(or `psql`) before the first deploy carrying this code:

```sql
ALTER TABLE clickable_streams
  ADD COLUMN IF NOT EXISTS bbox box GENERATED ALWAYS AS (
    box(
      point(min_lon::double precision, min_lat::double precision),
      point(max_lon::double precision, max_lat::double precision)
    )
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_clk_bbox_gist
  ON clickable_streams USING GIST (bbox);

ANALYZE clickable_streams;
```

After this, state-scale viewport queries that previously took 10s+ (and
killed the gunicorn worker at the 120s timeout under concurrent panning)
return in <100ms via `bbox && box(point(west, south), point(east, north))`.

## Validating

```sh
python scripts/validate_data.py
```

Exits 0 with a per-domain summary on success; nonzero with a list of
errors otherwise. The same script runs as part of
`tests/test_data_coverage.py`, so `pytest -q` will catch regressions
too.

## Adding a new state

1. Drop in `data/stocking/<STATE>.json` (and optionally
   `data/trout/<STATE>.json`).
2. Add the state code to the loader's state list in `stocking.py`
   (`STOCKING_BASELINE = {...}` dict literal) so it's picked up at
   import time.
3. Add an `agency_url` constant if you want a per-state default.
4. Run `pytest -q` -- the coverage test will flag a missing state.
