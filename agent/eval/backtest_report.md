# Prospecting Agent — Held-out-labels Backtest

_Generated 2026-06-19T17:28:16+00:00_  
Region: MD, VA, PA · designated reaches 36,378 · held-out 405 reaches across 204 whole rivers (masked by levelpathid, so discovery can't be faked by segment adjacency) · background 5,000 (sampled proxy negatives).

Deterministic suitability ranking (no LLM). Question: does it rank held-out trout water it was never told about above the mass?

## Signal ablation

ROC-AUC = held-outs vs the random background (easy negatives — trout water clusters, so this is optimistic). **Hard-neg AUC** = held-outs vs only *near-trout undesignated* reaches (the honest, harder test where topology can't separate and thermal/flow/access must).

| Mode | recall@50 | recall@100 | precision@50 | ROC-AUC | Hard-neg AUC | PR-AUC | access violations |
|---|---|---|---|---|---|---|---|
| topology | 0.123 | 0.247 | 1.0 | 0.994 | 0.981 | 0.981 | 0 |
| topology_thermal | 0.123 | 0.247 | 1.0 | 0.994 | 0.981 | 0.981 | 0 |
| topology_thermal_access | 0.069 | 0.069 | 0.56 | 0.772 | 0.316 | 0.203 | 0 |
| full | 0.069 | 0.069 | 0.56 | 0.772 | 0.316 | 0.203 | 0 |

_Hard-negative pool: 1558 near-trout undesignated reaches. Access violations (surfaced a known-private reach) = 0; of the top-250 surfaced, 141 carry an 'unverified access — confirm locally' flag (the access-data gap: we have access POINTS, not PAD-US public-land polygons)._

## Calibration (full model)

Predicted-confidence bucket vs actual held-out hit-rate.

| confidence | n | held-out hit-rate |
|---|---|---|
| 0.0-0.2 | 2835 | 0.0 |
| 0.2-0.4 | 271 | 0.0 |
| 0.4-0.6 | 381 | 0.005 |
| 0.6-0.8 | 1918 | 0.21 |
| 0.8-1.0 | 0 | - |

## Reading this

- **Topology carries the discovery** (recall/AUC rise from the topology-only baseline); thermal refines; **access is the actionability filter** (it gates out no-access reaches → access violations 0 in gated modes, at some recall cost).
- **Positive-unlabeled caveat:** non-held-out undesignated reaches are *unlabeled*, not negatives — a highly-ranked one may be a real discovery the backtest can't credit. So recall here is a **lower bound**, and PR-AUC uses a sampled background as proxy negatives.
- Designation is administrative (a proxy for fish presence), not field-survey ground truth — the best label available without a creel survey.
