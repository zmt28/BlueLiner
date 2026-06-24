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
from extending past the confluence onto the receiving river -- AND the
per-reach smoothed elevations (`maxelevsmo` / `minelevsmo`, cm) that
back the **stream gradient / elevation profile** (the panel's "Gradient"
tab, `/api/elevation_profile`). Bundled in the repo; loaded into
Postgres once at first boot.

Don't hand-edit. Regenerate by running:

```sh
pip install dbfread py7zr httpx       # dev-only deps
python scripts/build_nhdplus_vaa.py            # national (CONUS)
python scripts/build_nhdplus_vaa.py --vpu MA_02,MS_05   # fast regional
python scripts/build_nhdplus_vaa.py --list     # show discovered VPUs
```

The script **discovers** every CONUS Vector Processing Unit (VPU 01-18)
by listing the EPA NHDPlusV21 S3 bucket and picking the latest vintage
of each component archive -- no hand-maintained URL list (the old
hardcoded `REGIONS` was brittle because the `_<nn>` vintage suffixes are
not uniform across regions). For each VPU it downloads the
`NHDPlusAttributes` + `NHDSnapshot` archives, extracts
`PlusFlowlineVAA.dbf` (routing), `elevslope.dbf` (elevations), and
`NHDFlowline.dbf` (GNIS name), joins on ComID, and writes the gzipped
CSV. AK/HI/PR (VPU 19-21) are out of scope. The data is frozen at
NHDPlusV2's release, so re-run only to change coverage or the schema.

The `elevation_profile` feature needs a region's reaches present in this
table to work -- so a **national VAA rebuild + R2 republish (new version
prefix, see below) is required before the Gradient tab lights up outside
the bundled dev regions.** The schema added `maxelevsmo` / `minelevsmo`
after the original 6-column table shipped; `db.init_db` ALTERs the
columns in on existing deployments, and the Postgres COPY reads the CSV
header so it tolerates an older elevation-less file (the columns stay
NULL and the endpoint 404s until the new CSV is loaded).

## Clickable-streams network (`data/nhdplus/clickable_streams.geojson.gz`)

The geometry layer of fishing-relevant streams ("bluelining" network).
A reach is clickable if any of: StreamOrder >= 3; state-designated
trout water (any order); order >= 3 tributary of trout water; named
river order >= 5. Each feature carries `comid`, `levelpathid`,
`gnis_name`, `streamorder`, `lengthkm`, `trout_class`. Grouped by
`levelpathid` at runtime so a whole named river is one clickable unit.
~104K flowlines / ~6 MB gzipped for HUC-02 + HUC-05.

`trout_class` values: `wild_reproduction` (PA PASDA "With Tributaries"
+ VA DWR + MD DNR Use III/III-P cold water), `class_a` (PA),
`wilderness` (PA), `stocked` (PA + MD DNR Use IV/IV-P), `designated`
(legacy, no longer emitted), or `null` when no trout designation applies.

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

### Refreshing PAD-US (public-lands overlay)

`data/public_lands/public_lands.geojson.gz` is the bundled vector
overlay for the "Public lands" filter checkbox. National (~80-150 MB
gzipped) so it lives on R2 alongside the NHDPlus files. It's the
input to the public-lands PMTiles build (scripts/build_public_lands_tiles.sh);
the app serves those tiles from R2, not the GeoJSON directly.

PAD-US 4.0 ships as a single ~1.6 GB geodatabase ZIP on ScienceBase.
**ScienceBase routes files >1 GB through a captcha-gated, one-shot
downloader, so the build script cannot fetch it programmatically** --
the operator downloads the ZIP once by hand, the script reads from
disk.

The build script doesn't ingest PAD-US verbatim. Two angler-driven
filters are applied at build time:

- `Pub_Access IN ('OA', 'RA')` only -- Open Access and Restricted.
  UK (Unknown) and XA (Closed) parcels are dropped. Rendering
  unknown-access lands as "public-ish" would send anglers to locked
  gates; rendering closed-to-fishing refuges as public is similarly
  misleading. The frontend keys its style off this same field (green
  for OA, dashed yellow for RA).
- Easement layer dropped entirely. Conservation easements on private
  land restrict the owner's development rights, not the public's
  access rights; ~95% are not legally fishable without permission.

Interior holes smaller than ~30 acres are also stripped from each
polygon (National Forest features carry hundreds of tiny private-
inholding rings that dominate vertex count without being visually
meaningful at app zoom). If a future PAD-US vintage exposes a finer-
grained access taxonomy, revisit `KEEP_PUB_ACCESS` in the script.

To roll a new PAD-US vintage (4.0 -> 4.1 -> ...):

1. **Download the geodatabase ZIP** (one-time, in a browser):
   - Open <https://www.sciencebase.gov/catalog/item/652ef930d34edd15305a9b03>.
   - Click `PADUS4_0Geodatabase.zip` in the Attached Files section.
   - Solve the captcha and wait for the "Download File" button to
     activate (ScienceBase prepares the ~1.6 GB file on their side
     first; takes ~30 s).
   - Save the file to `data/public_lands/PADUS4_0Geodatabase.zip`.
2. **Run the builder** (~10-15 min wall clock; peak RSS ~8-12 GB
   during the pyogrio read -- PAD-US has very complex polygons in
   Alaska parks and BLM sections):
   ```sh
   pip install -r requirements-dev.txt
   python scripts/build_public_lands.py
   ls -lh data/public_lands/public_lands.geojson.gz
   ```
   (If you saved the ZIP elsewhere, pass `--gdb-zip <path>`.)
3. **Upload to R2** (reuse the same creds + endpoint as the NHDPlus
   files):
   ```sh
   aws --endpoint-url "$R2_ENDPOINT" s3 cp \
       data/public_lands/public_lands.geojson.gz \
       s3://bluelines-data/v1/public_lands.geojson.gz
   ```
4. **Rebuild + upload the vector tiles.** Public lands is served as
   PMTiles from R2 now -- there's no `public_lands` DB table anymore:
   ```sh
   INPUT=data/public_lands/public_lands.geojson.gz \
   R2_BUCKET=bluelines-data R2_PREFIX=v1 R2_ENDPOINT="$R2_ENDPOINT" \
     scripts/build_public_lands_tiles.sh --upload
   ```
   The client reads it via `VITE_PUBLIC_LANDS_TILES_URL` (already set in
   Render), so no redeploy is needed unless that env changes.

## Validating

```sh
python scripts/validate_data.py
```

Exits 0 with a per-domain summary on success; nonzero with a list of
errors otherwise. The same script runs as part of
`tests/test_data_coverage.py`, so `pytest -q` will catch regressions
too.

## Adding a new state

1. Drop in `data/stocking/<STATE>.json`, optionally
   `data/trout/<STATE>.json`, and optionally
   `data/access_points/<STATE>.json` (see schemas below). Baseline
   files are auto-discovered at import -- no Python changes needed.
2. If the state agency publishes an ArcGIS layer for stocking or
   access points, add an entry to `data/stocking/sources.json` /
   `data/access_points/sources.json` (see "Live feed registries"
   below) -- live results merge on top of the bundled baseline.
3. Run `python scripts/validate_data.py` and `pytest -q`.

### Live feed registries (`data/{stocking,access_points}/sources.json`)

Declarative list of verified state-agency ArcGIS `/query` endpoints,
mirroring `data/trout/sources.json`. Per-source keys:

- common: `state`, `label`, `url` (the `/query?where=...` endpoint),
  `agency_url` (public agency page cited in popups)
- stocking: `category` (popup label), `name_field`, `species_field`
  (free-text column) or `species_flags` (map of 0/1 flag columns to
  species labels, VA style), `season_months` ([start, end] when the
  feed carries no season), `dedupe` (collapse multi-segment polyline
  reach layers to one pin per named water per ~0.1 deg cell)
- access: `name_field`, `type_field` (normalized onto the canonical
  enum), `type_flags` (ordered map of Y/N amenity columns to types,
  first truthy flag wins -- PA/MD style), or `fixed_type`;
  `notes_field`; `access` (default `public`)

**Before adding an entry, verify the endpoint end-to-end:**

```sh
python scripts/verify_feed_sources.py               # registry only
python scripts/verify_feed_sources.py --candidates  # also data/*/candidates.json
```

It checks layer metadata, record count, real `f=geojson` output (the
runtime fetcher requires GeoJSON support), state-bbox geometry sanity,
and that every declared field exists in the layer schema. Unverified
leads live in `data/{stocking,access_points}/candidates.json` (same
shape); promote one into `sources.json` only after the script passes
it. Note: the Claude Code sandbox can reach only a few state GIS
hosts -- run the script from a dev machine or CI for everything else.

### `data/access_points/<STATE>.json` schema

Per-state list of angler access points (boat ramps, walk-in trails,
fishing piers, parking, wading spots). Schema:

```json
[
  {
    "name": "Glencoe / Monkton (Gunpowder Falls)",
    "lat": 39.5760, "lon": -76.6130,
    "type": "walk_in",
    "access": "public",
    "agency_url": "https://dnr.maryland.gov/publiclands/...",
    "notes": "NCR Trail parking; broad runs and slicks."
  }
]
```

- `type ∈ {boat_ramp, walk_in, pier, parking, wading_access}` -- drives
  marker glyph + color on the map.
- `access ∈ {public, permit, fee, private_easement}` -- drives a chip
  on the popup.
- `notes` is freeform and optional.
- Coordinates can be approximate (~100 m precision acceptable); the
  client renders a 22-pixel disc at any zoom.

A state-DNR live ArcGIS endpoint can be added to
`data/access_points/sources.json` once verified (see "Live feed
registries" above); live results merge on top of the baseline. Until
then, the bundled JSON is the only source.
