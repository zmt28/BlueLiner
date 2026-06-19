# Prospecting Agent — Held-out-labels Backtest

_Generated 2026-06-19T19:51:54+00:00_  
Region: MD, VA, PA · designated reaches 36,378 · held-out 405 reaches across 204 whole rivers (masked by levelpathid, so discovery can't be faked by segment adjacency) · background 4,000 (sampled proxy negatives).

Deterministic suitability ranking (no LLM). Question: does it rank held-out trout water it was never told about above the mass?

## Signal ablation

ROC-AUC = held-outs vs the random background (easy negatives — trout water clusters, so this is optimistic). **Hard-neg AUC** = held-outs vs only *near-trout undesignated* reaches (the honest, harder test where topology can't separate and thermal/flow/access must).

| Mode | recall@50 | recall@100 | precision@50 | ROC-AUC | Hard-neg AUC | PR-AUC | access violations |
|---|---|---|---|---|---|---|---|
| topology | 0.123 | 0.247 | 1.0 | 0.998 | 0.986 | 0.991 | 0 |
| topology_thermal | 0.123 | 0.247 | 1.0 | 0.998 | 0.986 | 0.991 | 0 |
| topology_thermal_access | 0.069 | 0.119 | 0.56 | 0.945 | 0.512 | 0.491 | 0 |
| full | 0.069 | 0.119 | 0.56 | 0.945 | 0.512 | 0.491 | 0 |

_Hard-negative pool: 447 near-trout undesignated reaches. Access violations (surfaced a known-private reach) = 0; of the top-250 surfaced, 139 carry an 'unverified access — confirm locally' flag (the access-data gap: we have access POINTS, not PAD-US public-land polygons)._

## Calibration (full model)

Predicted-confidence bucket vs actual held-out hit-rate.

| confidence | n | held-out hit-rate |
|---|---|---|
| 0.0-0.2 | 3240 | 0.0 |
| 0.2-0.4 | 186 | 0.0 |
| 0.4-0.6 | 217 | 0.009 |
| 0.6-0.8 | 762 | 0.529 |
| 0.8-1.0 | 0 | - |

## Reading this

- **Topology carries the discovery** (recall/AUC rise from the topology-only baseline); thermal refines; **access is the actionability filter** (it gates out no-access reaches → access violations 0 in gated modes, at some recall cost).
- **Positive-unlabeled caveat:** non-held-out undesignated reaches are *unlabeled*, not negatives — a highly-ranked one may be a real discovery the backtest can't credit. So recall here is a **lower bound**, and PR-AUC uses a sampled background as proxy negatives.
- Designation is administrative (a proxy for fish presence), not field-survey ground truth — the best label available without a creel survey.
