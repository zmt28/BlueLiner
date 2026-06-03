# Phase 0 Spike — Automated trout-source discovery

Feasibility + accuracy probe before building a discovery factory to scale trout
coverage from the current 10 states toward national. **Goal:** answer, with
numbers, whether per-state source discovery + wild/stocked classification can be
automated behind a light human gate — before investing in the full pipeline.

National trout coverage is ~30 coldwater states, not 50 (no salmonid fishery in
the Gulf/plains lowlands), so the real target is smaller than it looks.

## What's in this spike

| Module | Runs | Purpose |
|---|---|---|
| `scripts/discovery/lexicon.py` | offline | regulation-vocabulary token tables |
| `scripts/discovery/classify.py` | offline | label → wild / stocked / **FLAG** |
| `scripts/discovery/gold.json` | offline | the 10 shipped states as ground truth |
| `scripts/discovery/eval.py` | offline | grade the classifier vs gold |
| `scripts/discovery/catalogs.py` | **CI** | candidate endpoints (ArcGIS/CKAN/dir-walk) |
| `scripts/discovery/probe.py` | **CI** | layer metadata + scoring |
| `scripts/discovery/report.py` | offline | dossier + go/no-go memo |
| `scripts/discover_trout_sources.py` | both | CLI (`eval` offline, `discover` in CI) |
| `.github/workflows/trout-discovery-spike.yml` | CI | the open-egress discovery job |

The crawl/probe needs **open egress** to reach arbitrary state ArcGIS hosts, so
it runs in GitHub Actions, not the allowlisted sandbox. The classifier and its
eval are pure/offline.

## Test sets

- **Gold (10, ground truth):** MD VA PA NJ VT MA NY WV NC GA — real category
  vocabularies + the buckets the project actually assigned (`gold.json`).
- **Fresh (3):** **WI** (Class I/II/III), **MI** (Type 1–4), **CO** (western
  multi-species) — span different category schemes.
- **Negative control (1):** **TN** — known retired/points-only; the factory must
  independently tier it C/D, validating fallback/rot detection.

## Go/no-go gates

| Gate | Threshold | Status |
|---|---|---|
| Classifier auto-accuracy (gold) | ≥ 90% | ✅ **100%** (17/17) |
| Mis-bucket count (auto but wrong) | 0 | ✅ **0** |
| Discovery recall (gold endpoint in top-5) | ≥ 7/10 | ⏳ CI |
| Fresh states reaching ≥ tier-B | ≥ 3/3 | ⏳ CI |
| TN negative control → tier C/D | yes | ⏳ CI |
| Federal multi-state baseline identified | ≥ 1 | ⏳ research |

## Result so far (offline, `… eval`)

```
labels          : 23
auto-bucketed   : 17  (coverage 74%)
auto-accuracy   : 100%  (17/17)
mis-buckets     : 0
flagged (human) : 6
GATES: PASS
```

The classifier auto-resolves ~3/4 of the gold vocabulary at 100% precision with
**zero mis-buckets**, and correctly **flags** every genuinely state-specific term
(MD "Designated Use", NC/GA "Special Regulation", NY "Other", VT brook-trout
polygons, MA coldwater) for human review rather than guessing. Takeaway: the
classification half is trustworthy behind a human gate on ~1/4 of terms — the
wrong-on-the-map failure mode is designed out. A richer classifier that also
reads dataset name + agency context would likely auto-resolve several of the
flagged whole-layer names (e.g. a layer literally titled "Designated Use Trout").

## Remaining (CI / research)

1. **Run the discovery job** (`workflow_dispatch`) over `CO,MI,WI,TN` to measure
   discovery recall on the gold hosts and tier the fresh states; review the
   dossier artifacts.
2. **Federal baseline scan** — assess EBTJV/NFHP (East) and StreamNet/USFS/TU
   (West) as coarse multi-state baselines (Phase-3 lever).
3. If gates hold, proceed to **Phase 1** (config-driven `data/trout/sources.json`
   the build reads) and **Phase 2** (parallel per-state discovery → batch human
   review), turning "months, one state at a time" into a batch run + review pass.

## Classifier design note

Decision order is *strong signal beats ambiguous*: a wild **or** stocked token
auto-buckets; an ambiguous-only term (special regulation / trophy / Class II /
Type N / designated / other) always FLAGs. A wrong auto-bucket (a stocked stream
painted green) is worse than a deferred one, so the lexicon errs toward flagging.
Every edit to `lexicon.py` is re-graded by `eval.py` against the 10 gold states.
