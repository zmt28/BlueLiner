# Coverage survey & endpoint watch

This directory backs BlueLiner's standing "what GIS data are we still missing"
tooling. The data app classifies trout streams and shows stocking / access
points from ~per-state agency ArcGIS layers declared in
`data/{trout,stocking,access_points}/sources.json`. Coverage across the lower 48
is uneven and the ~30 state GIS servers flap, so discovery has to be a standing,
retrying activity rather than a one-shot.

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

## Relationship to the endpoint watch

The (narrow) 6h endpoint-watch re-verifies already-known specific candidates on
the same `endpoint-watch` side branch. The coverage survey is the complementary
broad, weekly recompute-the-gaps-and-go-find-something pass. Together: the watch
tells you when a known flaky endpoint recovered; the survey tells you what's
still uncovered and surfaces fresh leads to promote.
