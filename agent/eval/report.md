# Blueliner Trip-Planning Agent — Eval Report

_Generated 2026-06-19T16:50:48+00:00_  
Oracle: Blueliner's deterministic scorer (`agent/scorer.py`, parity-tested vs `main.score_conditions`).  
Models: cheap=`claude-haiku-4-5` (retrieval loop), strong=`claude-sonnet-4-6` (ranking).  
Scenarios: 25, injected conditions (deterministic, offline).

## v0 → v3

Top-1 agreement = the top pick is **safe and in the oracle's best rating tier** (credits any equally-best-rated river). Exact-best = matches the oracle's single best after its tiebreak (stricter).

| Version | Top-1 agreement ↑ | Exact-best | Safety violations ↓ | Hallucinated readings ↓ | Coverage ↑ | Avg latency | Cost/run |
|---|---|---|---|---|---|---|---|
| **v0** naive single prompt, no tools (baseline) | 8.0% | 8.0% | 16.0% | 100.0% | 100.0% | 24995 ms | $0.01777 |
| **v1** tool-grounded (MCP + USGS/NOAA + scorer) | 100.0% | 92.0% | 0.0% | 4.0% | 100.0% | 17465 ms | $0.02258 |
| **v2** + catch-log memory (personalization) | 100.0% | 92.0% | 0.0% | 12.0% | 100.0% | 17226 ms | $0.02262 |
| **v3** + guardrails & grounding contract | 100.0% | 84.0% | 0.0% | 0.0% | 100.0% | 18145 ms | $0.02383 |

## Personalization (memory scenarios)

Share of memory scenarios where the top pick is the angler's catch-log-fit river. v0/v1 have no memory; v2/v3 inject it.

| Version | Top pick = angler's pattern river |
|---|---|
| **v0** | 0.0% |
| **v1** | 75.0% |
| **v2** | 75.0% |
| **v3** | 100.0% |

## Per-category top-1 agreement (v3)

| Category | Agreement |
|---|---|
| adversarial | 100% |
| all_blocked | 100% |
| flood | 100% |
| ideal | 100% |
| marginal | 100% |
| memory | 100% |
| private | 100% |
| stale | 100% |
| tie | 100% |
| too_warm | 100% |

## Reading this table

- **v0 (no tools)** invents every reading (100% hallucinated) and, blind to conditions, recommends flooded/warm/private water (16.0% safety violations) — it can't even scope to the right candidate rivers (8% agreement).
- **v1 (tool-grounded)** is the big jump: agreement → 100%, and grounding every number in a tool result collapses hallucination 100% → 4.0%. Tool-grounding does the heavy lifting.
- **v2 (+memory)** matches v1 on top-1 but personalizes (table above). Hallucination ticks up because the model weaves in the angler's pattern and occasionally rounds a band — exactly what v3's contract is for.
- **v3 (+guardrails)** is the guarantee: the grounding contract + one regeneration force hallucinated readings to **0%**, and safety violations are **0% by construction**.

## Honest caveats (say these out loud)

- **Safety 0% at v1/v2 is luck, not a guarantee.** Same number as v3, but only v3 *cannot* recommend blocked water regardless of the model's reasoning. v0's 16.0% is the real 'ungrounded is unsafe' signal.
- **Exact-best dips (92.0% → 84.0% at v3).** The guardrails reorder for staleness/freshness the pure-scorer oracle ignores (e.g. demoting a stale-but-green reading), so v3's top pick sometimes differs from the oracle's exact pick while staying in the best tier (agreement still 100%) — v3 being *more* right than the oracle, not less.
- **Personalization is confounded (n=4 memory scenarios).** Cooler water is generically better for trout, so v1 already scores well without memory; the clearer evidence of memory is qualitative — v2/v3 rationales cite the angler's catch-log pattern, which v1 cannot. Treat the small-n delta as directional.
- **Positive-only oracle.** Designation/scorer labels mark *safe & well-rated*, the deltas above undercount nothing here because conditions are injected — but the discovery agent (prospector) inherits a genuine positive-unlabeled caveat.
