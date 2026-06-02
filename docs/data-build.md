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

The default lower-48 build downloads + extracts ~15–30 GB, which exceeds the
14 GB standard-runner disk. The workflow targets a **GitHub Team larger
runner**: create a **4-core Linux** runner (~16 GB RAM, ~150 GB SSD) named
`blueliner-build` under **Org → Settings → Actions → Runners → Larger
runners**. RAM is comfortable (~1–1.5 GB working set); **disk is the binding
constraint**. Cost is ~$1–3 per full national run at current larger-runner
rates.

### Secrets

Set on the repo/org: `R2_BUCKET`, `R2_ENDPOINT`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`.

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
