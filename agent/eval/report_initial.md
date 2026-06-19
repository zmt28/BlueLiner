# Blueliner Trip-Planning Agent — Eval Report

_Generated 2026-06-19T16:13:44+00:00_  
Oracle: Blueliner's deterministic scorer (`agent/scorer.py`, parity-tested vs `main.score_conditions`).  
Models: cheap=`claude-haiku-4-5` (retrieval loop), strong=`claude-sonnet-4-6` (ranking).  
Scenarios: 25, injected conditions (deterministic, offline).

## v0 → v3

Top-1 agreement = the top pick is **safe and in the oracle's best rating tier** (credits any equally-best-rated river). Exact-best = matches the oracle's single best after its tiebreak (stricter).

| Version | Top-1 agreement ↑ | Exact-best | Safety violations ↓ | Hallucinated readings ↓ | Coverage ↑ | Avg latency | Cost/run |
|---|---|---|---|---|---|---|---|
| **v0** naive single prompt, no tools (baseline) | 8.0% | 4.0% | 20.0% | 100.0% | 100.0% | 25029 ms | $0.01778 |
| **v1** tool-grounded (MCP + USGS/NOAA + scorer) | 96.0% | 88.0% | 4.0% | 36.0% | 100.0% | 16914 ms | $0.02259 |
| **v2** + catch-log memory (personalization) | 100.0% | 88.0% | 0.0% | 48.0% | 100.0% | 17719 ms | $0.02317 |
| **v3** + guardrails & grounding contract | 100.0% | 84.0% | 0.0% | 16.0% | 100.0% | 20841 ms | $0.02732 |

## Personalization (memory scenarios)

Share of memory scenarios where the top pick is the angler's catch-log-fit river. v0/v1 have no memory; v2/v3 inject it.

| Version | Top pick = angler's pattern river |
|---|---|
| **v0** | 25.0% |
| **v1** | 75.0% |
| **v2** | 100.0% |
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

- **v0** has no tools: it invents flows/temps (hallucination high) and has no safety backstop.
- **v1** grounds every reading in a tool result; agreement jumps and hallucinated readings collapse toward 0.
- **v2** adds catch-log memory: it breaks ties toward the angler's productive conditions (see the `memory-*` scenarios).
- **v3** adds the deterministic guardrails: **safety violations → 0** and the grounding contract forces hallucinated readings to 0.
