# Coverage survey & endpoint watch

This directory backs BlueLiner's standing "what GIS data are we still missing"
tooling. The data app classifies trout streams and shows stocking / access
points from ~per-state agency ArcGIS layers declared in
`data/{trout,stocking,access_points}/sources.json`. Coverage across the lower 48
is uneven and the ~30 state GIS servers flap, so discovery has to be a standing,
retrying activity rather than a one-shot.

Two complementary passes run on their own schedules and both commit reports to
the long-lived **`endpoint-watch`** side branch (keeping `main` clean):

- **Coverage survey** (weekly, broad) — recompute the gaps across the lower 48
  and go find fresh candidate layers.
- **Endpoint watch** (6-hourly, narrow) — re-probe the specific endpoints we're
  already waiting on and capture data the instant a flaky server recovers.

Together: the survey tells you what's still uncovered and surfaces fresh leads;
the watch tells you when a known flaky endpoint recovered and grabs what we need.

## Coverage survey (`scripts/coverage_survey.py`, weekly)

Purpose: the broad, weekly **"what's missing across the lower 48 and what did we
find"** pass.

- Recomputes per-state coverage gaps for trout / stocking / access directly from
  the three `sources.json` registries (a state with a source entry is covered;
  computed in code, never hardcoded).
- For each **fillable** gap it discovers candidate state-agency ArcGIS layers:
  walking the state's known agency host root(s) (consolidated from
  `gis_endpoint_verify.SERVER_ROOTS`/`ORG_CATALOGS` + `discovery.catalogs`) and
  ArcGIS-Online search with datatype keywords, keyword-filtered, then a **light**
  metadata + count probe (not a full verify) of the top ~5.
- Trout/stocking gaps in states with negligible coldwater trout (a curated
  `TROUT_STATES` set; e.g. FL/LA/MS and largely KS/NE/OK/SD/IL/IN/ND/AL) are
  marked **"expected none"**, not "to discover" -- the survey wastes no probes
  there. Access gaps apply to all states (boat ramps / fishing access exist
  everywhere). Western trout states (ID/MT/OR/WA/NM/NV) already get baseline
  wild-trout coverage from the range-wide NATIVE overlays
  (WCT/BULL/RBT/YCT/BCT/RGCT/GILA), so a state trout source there only **adds**
  management tiers; the report flags this.
- Cadence: `schedule` weekly (Mondays 07:00 UTC) + manual dispatch, via
  `.github/workflows/coverage-survey.yml`.
- Output: `gis_verify_out/COVERAGE.md` (coverage matrix + per-gap discovered
  candidates + a one-line summary), committed to the long-lived `endpoint-watch`
  side branch, plus the matrix to the Actions step summary. Always exits 0.

**Report-only.** The survey never edits any `sources.json` / `candidates.json` --
picking the right layer and designing its `field_map` / `species_flags` /
`type_flags` needs human judgment. COVERAGE.md is the discovery worklist that
**feeds** the candidate-promotion pipeline.

## Endpoint watch (`scripts/endpoint_watch.py`, 6-hourly)

Purpose: the narrow, frequent pass that captures what we need the moment a
specific flaky/retired server we're waiting on comes back.

### What it watches

1. **`watchlist.json`** (this dir) -- INVESTIGATION endpoints. These are *not*
   candidate feeds; they're one-time captures we want when a retired/down server
   recovers. Each entry:

   ```json
   {
     "id": "stable-slug",
     "kind": "field_dump | discover | verify",
     "state": "MD",
     "url": "https://.../MapServer/0",
     "field": null,
     "note": "what we're after and why"
   }
   ```

   - **`field_dump`** -- probe a layer; if up, capture `name`/`geometry`/`fields`
     + 3 sample features. If `field` is set, also dump that field's distinct
     values. `field: null` means "dump everything so a human can identify the
     field," then set `field` for the next run.
   - **`discover`** -- if up, enumerate the folder's services / search the AGOL
     org and list trout/fish-named layers with record counts.
   - **`verify`** -- run the candidate 4-check (meta, count, f=geojson sample,
     in-state bbox) on a URL.

2. **`data/stocking/candidates.json`** + **`data/access_points/candidates.json`**
   -- the unverified feed leads. The watcher folds these in automatically as
   `verify`-kind entries. A candidate that PASSES the 4-check is flagged
   **READY TO PROMOTE** -- the watcher never edits `sources.json`; promotion
   stays human-reviewed.

### Flow

`scripts/endpoint_watch.py` loads the watchlist + both candidate files, probes
each entry (bounded timeout, a couple retries for flap tolerance), and writes a
markdown report. A DOWN host is reported as down, never an error -- the script
always exits 0 (it's a watcher, not a gate).

The report leads with a status table (`id | state | kind | UP/DOWN | captured`)
so a recovery is obvious at a glance, then per-entry detail for the reachable
ones.

### Where the report lands

`.github/workflows/endpoint-watch.yml` runs the watcher on a 6-hour cron (plus
`workflow_dispatch`):

- The **job step summary** (Actions tab) is the at-a-glance notification.
- The full report, including field dumps, is committed to the long-lived
  **`endpoint-watch`** branch as `gis_verify_out/WATCH.md` -- retrievable without
  cluttering `main`.

> The Claude Code sandbox's egress allowlist blocks most state GIS hosts, so a
> **local** run of `endpoint_watch.py` (or `coverage_survey.py`) shows most
> entries DOWN. That's expected; the scheduled Actions runner has open egress
> and is where the probes fire.
