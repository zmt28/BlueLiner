# Blueliner Prospecting Agent (discovery, on LangGraph)

Where the trip-planner *retrieves and ranks* known rivers, the prospector
*generates and validates hypotheses* about water that isn't designated trout
water but has the characteristics of it — undesignated reaches that are
tributary-proximate to known trout water, run cold, and have public access.

**The Red Ventures analogy (the slide):** this is RV's core competency in
different nouns — find undervalued "inventory" (unrecognized fishable water),
qualify it on the binding constraint (public access = actionability), score it by
confidence (lead scoring), keep a human in the loop to confirm (verification), and
compound a data advantage as confirmations accrue (the flywheel).

## Architecture — a LangGraph StateGraph

```
generate_candidates → gather_evidence ─(ungauged?)→ infer_thermal → score
                            │                                         │
                            └──────────────(gauged)───────────────────┘
                                            ▼
        score → reflect_verify → rank → human_confirm → update_flywheel
```

LangGraph owns the **control flow, the conditional ungauged branch, the
human-in-the-loop `interrupt()`, and durable checkpointing (`SqliteSaver`)**.
Tools stay on MCP; the scorer (`suitability.py`) and guardrails are plain Python.
Single agent expressed as a graph — not a multi-agent swarm.

| File | Role |
|---|---|
| `prospector_state.py` | typed `ProspectState` threaded through nodes |
| `prospector_nodes.py` | node functions (generate/gather/infer/score/verify/rank/confirm/flywheel) |
| `prospector_graph.py` | `StateGraph` wiring, conditional edges, checkpointer, `run_discovery()` |
| `suitability.py` | deterministic coldwater scorer (grounding tool + ranking baseline + ablation) |
| `signals.py`, `reach_data.py` | feature extraction + bundled-data layer (NHDPlus/PAD-US/trout) |
| `eval/backtest.py` | held-out-labels backtest (recall@k, PR-AUC, calibration, ablation) |
| `prompts/prospector_system.md` | discovery-agent rationale prompt (hypotheses, grounding, uncertainty) |
| new MCP tools | `get_undesignated_reaches`, `get_reach_topology`, `get_reach_access`, `get_designation_status`, `coldwater_suitability` |

## Run

```bash
pip install -r agent/requirements.txt        # adds langgraph + sqlite checkpointer
export ANTHROPIC_API_KEY=sk-ant-...

python -m agent.eval.backtest --states MD,VA,PA   # the centerpiece backtest -> backtest_report.md
python -m agent.prospector_graph --states MD --shortlist 12   # run the graph (headless)
```

## What the backtest shows (honest)

Topology is a near-perfect **lead generator** (ROC-AUC 0.99; even vs hard
negatives ~0.98). Enforcing **access** — the binding actionability constraint —
collapses recall (AUC 0.77) because we only have access *points*, not PAD-US
public-land *polygons* (retired to vector tiles), and they skew to private
easements on trout water. **The bottleneck is access-data coverage, not the
model** — the RV "qualify on the binding constraint" punchline, and a roadmap
lever (wire PAD-US polygons back in). Full methodology + honest caveats
(river-level masking, hard negatives, size-is-negative-for-trout, positive-
unlabeled) in `LEARNINGS.md` §6 and `eval/backtest_report.md`.

## Design decisions (measured restraint — the "what I chose not to build" slide)

- **LangGraph, not LangChain, and only for orchestration.** Adopted for explicit
  stateful control flow, the human-confirm interrupt, and durable checkpointing;
  tools stayed on MCP and the scorer/guardrails stayed plain Python. No
  higher-level chains.
- **LangGraph here, a hand-written loop for the trip-planner.** The trip-planner
  is linear and didn't need a graph; the prospector has real branching + a human
  interrupt + proactive resumption that do. Right tool for each.
- **Deterministic gather, LLM only on the shortlist.** `gather_evidence` calls the
  tools deterministically (exhaustive + free); the strong model writes the
  rationale once over the verified shortlist. No per-candidate LLM loop — it adds
  cost/latency without improving recall. Confidence stays deterministic (calibrated
  in the backtest); the LLM explains, it does not re-score.
- **Single agent, not multi-agent.** One discovery graph; no separate
  topology/thermal/access agents + orchestrator (would add latency/cost without
  improving recall at this scale).

## Artifacts (`eval/`)
- `backtest_report.md` / `backtest_results.json` — the held-out backtest + ablation + calibration.
- `sample_prospect_trace.md` / `.json` — a headless graph run (node flow, grounded prospects).
- `sample_prospect_interrupt.json` — the human-confirm interrupt pausing and resuming durably via the checkpointer.
