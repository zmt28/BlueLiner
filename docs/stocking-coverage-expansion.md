# Stocking coverage expansion — playbook & worklist

_Last updated: 2026-06-25. Owner-of-record: the candidate-promotion pipeline
(`coverage-survey` → `candidates.json` → `verify` → `sources.json`)._

## Why stocking is its own problem

Rivers, elevation, the clickable-stream network, and trout-class tagging are all
derived from **national NHDPlus** data — once built, they cover the lower 48.
**Stocking is not.** It comes from **per-state curated baselines**
(`data/stocking/<ST>.json`) plus **live agency ArcGIS feeds**
(`data/stocking/sources.json`). So unlike everything else, stocking coverage is
state-by-state and only exists where we've explicitly sourced it. That's why
"so many rivers" show no stocking — it's a sourcing gap, not a bug.

Two render-side fixes already make the *existing* data show up correctly:
- **#181** — geometry hardening (`_geom_2d`/`_clean_coords`): a feed publishing
  4-ordinate (XYZM) or null-coordinate features used to drop its *entire*
  state (`kept 0, skipped N`). New feeds inherit this robustness.
- **#185** — per-gauge proximity: a long river's "stocked nearby" is probed at
  every gauge, not just the centroid, so feeds actually surface on the panel.

## Gap taxonomy (as of 2026-06)

Computed from the registries, not hardcoded (see `scripts/coverage_survey.py`).

| Bucket | States | Action |
| --- | --- | --- |
| **Live feed today** (17) | CA CO CT DE GA MA MD MT ND NH NJ NY PA TN UT VA VT WV | done (MA added 2026-06) |
| **A. First-light** — trout state, *zero* stocking data | RI, TX (+ WA, native-overlay-deprioritized) | high value, small |
| **B. Baseline-only** — sparse static points, no live feed (17 left) | AR AZ IA ID KY ME MI MN MO NC NM NV OH OR SC WI WY | the bulk of the opportunity |
| **C. Expected-empty** — warmwater, negligible trout | AL FL IL IN KS LA MS NE OK SD | **skip** — "no data" is *correct* here |

> The honest ceiling: most bucket-B agencies publish stocking as **PDF/HTML
> schedules, not queryable GIS**. Keyword discovery mostly returns noise for
> them, so they stay on curated baselines — and that's the right end state.
> Expect a *handful* of new live feeds from bucket B over time, not 17.

## The pipeline (existing tooling — do not rebuild)

```
coverage_survey.py (weekly, CI)   → discovers candidate ArcGIS layers per gap
  → gis_verify_out/COVERAGE.md        (worklist on the endpoint-watch branch)
candidates.json (human picks layer)  → draft {state,label,url,field_map}
gis_verify_request.txt "ST|url"      → CI field-dump (all fields + 3 samples)
verify_feed_sources.py --candidates  → CI verify: metadata + count + geojson sample
  → promote to data/stocking/sources.json   (PR; registries are NEVER auto-edited)
stocking.py (runtime)                → fetch + parse; degrades to baseline on failure
```

- **`coverage-survey.yml`** (weekly + dispatch) — broad discovery; report-only.
- **`gis-endpoint-verify.yml`** (dispatch, or push on the verify branch) — runs
  `verify_feed_sources.py --candidates` (the promotion gatekeeper) and, when
  `scripts/gis_verify_request.txt` has `ST|url` lines, an ad-hoc **full field
  dump** committed to `gis_verify_out/REPORT.txt`.
- **`endpoint-watch.yml`** (6-hourly) — re-probes known flaky candidates.

The sandbox **cannot reach state GIS hosts** (egress allowlist) — all discovery
and verification runs in Actions. Do not probe locally.

## The promotion template (per source)

1. **Discover** — dispatch `coverage-survey.yml`; read `COVERAGE.md` on the
   `endpoint-watch` branch. Triage: most "stocking" hits are keyword noise
   ("Treatment **Plant**", "carbon **stock**", "Pheasant **Stocking**", habitat
   layers). Keep only official trout/fish *stocking point* layers.
2. **Field-dump** — put `ST|<layer-url>` lines in `scripts/gis_verify_request.txt`,
   push to a branch, dispatch `gis-endpoint-verify.yml` on it. Read the real
   schema from the committed `REPORT.txt`.
3. **Design the `field_map`** from the real columns (see checklist below).
4. **Promote** — add the source(s) to `data/stocking/sources.json`; remove the
   promoted lead from `candidates.json`.
5. **Validate locally — BOTH:**
   - `python scripts/validate_data.py` (schema/bbox lint), AND
   - **`pytest -q` (the FULL suite, not `-k stock`).** ← see why below.
6. **Verify in CI** — dispatch `gis-endpoint-verify.yml` on the branch;
   `verify_feed_sources` parses the source *as configured* and confirms
   metadata + count + a real geojson sample.
7. **PR** — keep it to the registry files (revert any scratch
   `gis_verify_request.txt` change). Merge → live on the next `/api/rivers`
   assembly (short TTL; no rebuild, no cutover).

### ⚠️ Always run the FULL pytest suite on a registry change

A `sources.json`/`candidates.json` edit is **not** "just data." Two tests pin
registry-derived **snapshot counts**, and they (correctly) move when you
promote:
- `tests/test_coverage_survey.py::test_real_registry_gaps_match_known_worklist`
  — the per-datatype "no source" gap counts.
- `tests/test_endpoint_watch.py::test_load_entries_includes_watchlist_and_candidates`
  — the seeded candidate count.

Promoting MA dropped stocking gaps 31→30 and candidates 8→7. Both **must** be
updated in the same PR. Running only `-k stock` misses them (that's exactly how
#186's first CI run went red). **Run `pytest -q` and update the snapshots.**

## `field_map` design checklist

- `name_field` — the water-name column (`NAME`, `WATER`, `LOCATION`, `GNIS_Name`…).
- Species: `species_flags` (0/1 columns → labels, VA-style) **or** `species_field`
  (free-text column). Omit both if the feed carries no species (→ empty list).
- `category` — fixed popup label; or `category_from_props: true` to prefer the
  feed's own category column.
- `season_months` — `[start, end]` fixed window when the feed has no numeric
  season. A free-text "Season" ("Spring"/"Fall") does **not** parse → defaults
  to year-round (consistent with most current sources).
- `dedupe: true` — for multi-segment polyline/reach layers (collapse to one pin
  per named water per ~0.1°). **Not** needed for clean point layers.
- `url` — append `/query?where=1%3D1` (the loader adds `outFields`/`f=geojson`).

## Worked example — MA pilot (#186)

Survey surfaced MassWildlife's official 2025 layers; the field dump confirmed
clean point geometry with a `NAME` column and **no per-location species**:

```json
{ "state": "MA", "label": "MassWildlife Stocked Lakes & Ponds (2025)",
  "url": ".../2025_Fish_Stocking_Locations_WFL1/FeatureServer/0/query?where=1%3D1",
  "category": "Stocked lake/pond (MassWildlife)", "name_field": "NAME" }
{ "state": "MA", "label": "MassWildlife Stocked Rivers & Streams (2025)",
  "url": ".../FeatureServer/1/query?where=1%3D1",
  "category": "Stocked river/stream (MassWildlife)", "name_field": "NAME" }
```

`name_field: NAME`, no `species_field`, no `dedupe`, no `season_months`. Took MA
from an 11-point baseline to **~591 live points** (239 lakes/ponds + 352
rivers/streams). Verified PASS in CI.

## Standing automation (no manual probing needed)

- **`coverage-survey.yml`** — Mondays 07:00 UTC: recompute gaps + rediscover
  candidates for the 24/37 reachable gap hosts. The next fillable layer surfaces
  on its own.
- **`endpoint-watch.yml`** — every 6h: capture a known flaky candidate the
  moment its server recovers.
- **Registry health** — dispatching `gis-endpoint-verify.yml` re-verifies every
  live source. As of 2026-06-25 all 25 stocking + 33 access sources PASS (no
  drift). Re-run after any promotion, and periodically to catch a feed that a
  state retires or moves.

## Status / next actions

- ✅ MA promoted (#186).
- ⏭ Bucket A: RI, TX — re-run discovery; if no GIS layer, leave on baseline.
- ⏭ Bucket B: promote opportunistically as the weekly survey surfaces real
  layers; do **not** force-fill PDF-only states.
- ⛔ Bucket C: leave empty (correct).
