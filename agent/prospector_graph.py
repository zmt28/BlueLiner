"""The prospector as an explicit LangGraph StateGraph.

    generate_candidates → gather_evidence ─(ungauged?)→ infer_thermal → score
                                │                                         │
                                └────────────────(gauged)────────────────┘
                                                  ▼
                       score → reflect_verify → rank → human_confirm → update_flywheel

LangGraph owns the control flow, the conditional ungauged branch, the
human-in-the-loop interrupt, and durable checkpointing (SqliteSaver). Tools stay
on MCP; the scorer/guardrails are plain Python. Single agent expressed as a
graph — NOT a multi-agent swarm.

Why LangGraph here (and not for the linear trip-planner): this workflow has real
branching, a human-confirm interrupt, and long-running/proactive resumption that
benefit from durable checkpoints. The trip-planner is linear and didn't need it —
that contrast is the "right tool for the job" slide.
"""

from __future__ import annotations

import argparse
import json

from langgraph.graph import END, START, StateGraph

from . import config, prospector_nodes as N
from .prospector_state import ProspectState


def build_graph() -> StateGraph:
    g = StateGraph(ProspectState)
    g.add_node("generate_candidates", N.generate_candidates)
    g.add_node("gather_evidence", N.gather_evidence)
    g.add_node("infer_thermal", N.infer_thermal)
    g.add_node("score", N.score)
    g.add_node("reflect_verify", N.reflect_verify)
    g.add_node("rank", N.rank)
    g.add_node("human_confirm", N.human_confirm)
    g.add_node("update_flywheel", N.update_flywheel)

    g.add_edge(START, "generate_candidates")
    g.add_edge("generate_candidates", "gather_evidence")
    # Conditional ungauged branch.
    g.add_conditional_edges("gather_evidence", N.route_after_gather,
                            {"infer_thermal": "infer_thermal", "score": "score"})
    g.add_edge("infer_thermal", "score")
    g.add_edge("score", "reflect_verify")
    g.add_edge("reflect_verify", "rank")
    g.add_edge("rank", "human_confirm")
    g.add_edge("human_confirm", "update_flywheel")
    g.add_edge("update_flywheel", END)
    return g


def run_discovery(states, shortlist_k: int = 12, headless: bool = True,
                  thread_id: str = "demo", checkpoint_path: str | None = None) -> dict:
    """Run the graph end-to-end (headless skips the human-confirm interrupt).
    The SqliteSaver checkpointer makes the interrupt durable across restarts."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    builder = build_graph()
    path = checkpoint_path or str(config.LOG_DIR / "prospector_checkpoints.sqlite")
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    initial: ProspectState = {
        "region": {"states": list(states), "shortlist_k": shortlist_k,
                   "headless": headless},
        "trace": [],
    }
    with SqliteSaver.from_conn_string(path) as cp:
        graph = builder.compile(checkpointer=cp)
        return graph.invoke(initial, config={"configurable": {"thread_id": thread_id}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="MD")
    ap.add_argument("--shortlist", type=int, default=12)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    states = tuple(s.strip().upper() for s in args.states.split(","))

    final = run_discovery(states, shortlist_k=args.shortlist, headless=True)
    if args.json:
        print(json.dumps({k: final.get(k) for k in
                          ("ranked", "excluded", "trace", "usage")},
                         indent=2, default=str))
        return
    print("\n=== Graph trace ===")
    for t in final.get("trace", []):
        print(" ", {k: v for k, v in t.items()})
    print("\n=== Prospects ===")
    for i, p in enumerate(final.get("ranked", []), 1):
        print(f"{i}. {p.get('descriptor')}  (confidence {p.get('confidence')})")
        for e in p.get("evidence", []):
            print(f"     - {e}")
        if p.get("why_not_higher"):
            print(f"     why not higher: {p['why_not_higher']}")
    if final.get("excluded"):
        print("\n=== Excluded (guardrails) ===")
        for x in final["excluded"]:
            print(f"   x comid {x['comid']}: {x['reason']}")


if __name__ == "__main__":
    main()
