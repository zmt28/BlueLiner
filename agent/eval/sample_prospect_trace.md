# Sample prospector graph trace (headless backtest mode)

LangGraph StateGraph run over Maryland undesignated reaches. Deterministic gather/score/guardrails; one strong-model call writes the grounded rationale.

## Node flow

| node | detail |
|---|---|
| `generate_candidates` | n_region=6012, shortlist=10 |
| `gather_evidence` | n=10, ungauged=10 |
| `infer_thermal` | inferred=10 |
| `score` | n=10 |
| `reflect_verify` | kept=10, dropped=0 |
| `rank` | n=10, grounding_ok=True, unsourced=[], cost_usd=0.03847 |
| `human_confirm` | mode=headless_skip |
| `update_flywheel` | recorded=0, promoted=0 |

_The `infer_thermal` node fired via the conditional ungauged branch; `rank` passed the grounding check (every cited number traces to a tool result); `human_confirm` is the human-in-the-loop gate (skipped headless)._

**Cost:** $0.03847 · one `claude-sonnet-4-6` call (2543 in / 2056 out). Deterministic topology ranks the full region for free; the LLM touches only the shortlist.

## Top prospects (grounded, hypothesis-framed)

### 1. Elks Run — confidence 0.733 (suitability 0.978)
- Same-named reach as designated trout water Elks Run; topology distance 0.0 mi — likely the same drainage network
- Stream order 3; reach length 0.001 km suggests a short connector or headwater segment with potential cold tributary character
- Public access confirmed; nearest access point Potomac River – Dargan/Shinham, 2.21 mi

_why not higher:_ Ungauged — water temperature entirely inferred; no same-network gauge identified  
_access:_ public | confirm access locally and verify current regulations

### 2. Difficult Run — confidence 0.733 (suitability 0.978)
- Same-named reach as designated trout water Difficult Run; topology distance 0.0 mi
- Stream order 4; nearest access point Anglers Inn only 0.61 mi away — closest walk-in proximity of all prospects listed
- Public access confirmed via Anglers Inn access point

_why not higher:_ Ungauged — water temperature entirely inferred; no same-network gauge identified  
_access:_ public | confirm access locally and verify current regulations

### 3. Dutchman Creek — confidence 0.733 (suitability 0.978)
- Same-named reach as designated trout water Dutchman Creek; topology distance 0.0 mi
- Stream order 3; reach length 0.008 km suggests compact headwater or near-confluence segment
- Public access confirmed; nearest access point Brunswick, 0.83 mi

_why not higher:_ Ungauged — water temperature entirely inferred; no same-network gauge identified  
_access:_ public | confirm access locally and verify current regulations
