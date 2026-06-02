# Data build & ship runbook

How the static reference datasets get rebuilt and published to R2. These are
**data releases**, not per-request work — run them when source data changes
(an NHD vintage refresh, a Phase-2 trout-enrichment change) or on a deliberate
cadence. The app reads the published artifacts from R2; it never builds them.

## Datasets

| Artifact | Builder | Served as |
| --- | --- | --- |
| `clickable_streams.geojson.gz` | `scripts/build_clickable_streams.py` | Postgres (viewport GiST), via `DATA_BASE_URL` |
| `streams.pmtiles` | `scripts/build_stream_tiles.sh` (from the gz above) | PMTiles on R2, via `VITE_STREAM_TILES_URL` |
| `vaa.csv.gz` | `scripts/build_nhdplus_vaa.py` | Postgres, via `DATA_BASE_URL` |
| `public_lands.geojson.gz` | `scripts/build_public_lands.py` | Postgres, via `DATA_BASE_URL` |

This runbook covers the **clickable-streams** path (geometry + `trout_class`).
The others follow the same shape.

## Option 1 — GitHub Action (recommended)

`.github/workflows/data-build.yml`, triggered from **Actions → Data build →
Run workflow**. Inputs:

- **regions** — VPU ids, comma-separated (blank = all 21 lower-48). Use a
  subset (e.g. `02,05`) for a fast smoke build.
- **r2_prefix** — versioned prefix to publish under (`v1`, `v2`, …). Publish to
  a *new* prefix for an atomic, rollback-able release (see Cutover).
- **upload** — leave unchecked for a dry run (build + validate, no publish).

The job: installs the geo toolchain + tippecanoe → builds the gz (+ a
`clickable_streams.manifest.json` provenance sidecar) → runs a sanity gate
(fails a national run under 500k features) → builds the PMTiles → publishes all
three to `s3://$R2_BUCKET/<prefix>/` when **upload** is checked. The manifest is
echoed to the job summary.

### Runner

Runs on the **free standard `ubuntu-latest`** — no Team plan or larger runner
needed. The builder is VPU-streaming and **deletes each region's extract after
emitting it**, so peak disk stays at ~one region (a couple GB) regardless of
how many regions are built, comfortably inside the standard runner's ~14 GB
disk. (Use `--keep-extracts` locally if you want to retain them for repeat
runs.)

### Secrets

Set on the repo/org: `R2_BUCKET` (this project's bucket is `bluelines-data`),
`R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

## Option 2 — local build

Needs the dev-only geo toolchain (not in `requirements.txt`):

```bash
pip install dbfread py7zr httpx geopandas shapely
brew install tippecanoe   # or build from felt/tippecanoe

# Build (all regions; archive vintages auto-discovered from S3):
python scripts/build_clickable_streams.py \
    --manifest data/nhdplus/clickable_streams.manifest.json

# Faster iteration:
python scripts/build_clickable_streams.py --regions 02,05      # a subset
python scripts/build_clickable_streams.py --skip-download      # reuse cache

# Tiles:
scripts/build_stream_tiles.sh                                  # -> $TMPDIR/streams.pmtiles

# Publish (same env the Action uses):
R2_BUCKET=… R2_PREFIX=v1 R2_ENDPOINT=… \
AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=… \
    scripts/build_stream_tiles.sh --upload
```

## Trout sources & the MD seed fallback

Trout tagging pulls from three live state ArcGIS servers (VA DWR, MD DNR, PA
PASDA). Each fetch retries with backoff; if a source stays unreachable it's
recorded in the manifest's `trout_sources` map and, unless `--require-trout` is
set, the build proceeds with that state untagged.

MD's DNR server (`dnr.geodata.md.gov`) flaps, so MD has a committed fallback:
`data/nhdplus/MD_designated_comids.json` — the set of NHDPlusV2 COMIDs tagged
`designated` by a prior **live** build. When the live MD endpoint is down the
builder tags MD from this seed instead (manifest shows `MD: bundled-seed`), so
a release stays MD-complete and `--require-trout` still passes. COMIDs are
stable NHDPlusV2 identifiers, so the seed is equivalent to a live fetch until MD
changes its designations.

Regenerate the seed from a fresh live build (run when MD's endpoint is healthy):

```bash
python - <<'PY'
import gzip, json, subprocess
src = "data/nhdplus/clickable_streams.geojson.gz"
sha = subprocess.run(["git","log","-1","--format=%h","--",src],
                     capture_output=True, text=True).stdout.strip()
fc = json.load(gzip.open(src, "rt"))
comids = sorted({int(f["properties"]["comid"]) for f in fc["features"]
                 if f["properties"]["trout_class"] == "designated"})
json.dump({"state":"MD","trout_class":"designated",
           "source":"Maryland DNR Designated Use Trout",
           "captured_from":f"{src} @ {sha}","comid_count":len(comids),
           "comids":comids},
          open("data/nhdplus/MD_designated_comids.json","w"),
          separators=(",",":"))
print(f"{len(comids):,} COMIDs")
PY
```

## Cutover & rollback

Publishing to a **new** prefix (`v2/`) is non-destructive — nothing changes
until you point the app at it:

1. Publish to `v2/` (Action with `r2_prefix: v2`, upload checked).
2. Set `VITE_STREAM_TILES_URL=<base>/v2/streams.pmtiles` (and `DATA_BASE_URL`'s
   prefix for the GeoJSON/Postgres path) in the Render build env, redeploy.
3. **Rollback** = point the env vars back at `v1/` and redeploy. No rebuild.

## The manifest

`clickable_streams.manifest.json` ships next to each artifact and records what
the build actually was — `regions`, the exact NHD archive **vintages** resolved
per VPU, `feature_count`, the `trout_class` histogram, and the `git_sha`. It's
the answer to "what's live right now?" and makes a release reproducible.
