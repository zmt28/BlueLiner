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
