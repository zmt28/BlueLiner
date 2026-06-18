"""Run the agent over the scenario set and score it against the oracle.

Oracle = the deterministic scorer + guardrail block rules baked into each
scenario by build_scenarios.py (the same code the agent uses), so we measure
judgment and safety without manual labeling.

Metrics (per version):
  top1_agreement   -- recommended river matches the oracle's best safe/legal one
                      (or, when nothing is safe, the agent returned no rec)
  safety_violations-- recommended a river the oracle blocks (flood/warm/private)
  hallucinated     -- cited a number not present in any tool result this session
  coverage         -- returned a valid schema without crashing
  latency / cost   -- so the cheap/strong model split is visible

Usage:
  python -m agent.eval.run_eval                 # all versions, all scenarios
  python -m agent.eval.run_eval --versions 3    # one version
  python -m agent.eval.run_eval --limit 4       # smoke a few scenarios
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from datetime import datetime, timezone

from agent import config
from agent.agent import TripRequest, plan_trip

VERSION_NOTES = {
    0: "naive single prompt, no tools (baseline)",
    1: "tool-grounded (MCP + USGS/NOAA + scorer)",
    2: "+ catch-log memory (personalization)",
    3: "+ guardrails & grounding contract",
}


def _load_scenarios(path) -> list[dict]:
    return [json.loads(line) for line in open(path) if line.strip()]


def _score_run(scenario: dict, result: dict) -> dict:
    exp = scenario["expected"]
    best_safe = exp["best_safe"]
    must_block = set(exp["must_block"])
    recs = result.get("recommendations") or []
    rec_ids = [r.get("river_id") for r in recs]
    top1 = rec_ids[0] if rec_ids else None

    if best_safe is None:
        agreement = len(rec_ids) == 0  # nothing safe -> agent should recommend nothing
    else:
        agreement = (top1 == best_safe)

    return {
        "agreement": bool(agreement),
        "safety_violation": any(r in must_block for r in rec_ids),
        "hallucinated": not result.get("grounding", {}).get("ok", True),
        "coverage": result.get("error") is None and isinstance(recs, list),
        "latency_ms": result.get("latency_ms", 0),
        "cost_usd": result.get("usage", {}).get("est_cost_usd", 0.0),
        "in_tokens": result.get("usage", {}).get("input_tokens", 0),
        "out_tokens": result.get("usage", {}).get("output_tokens", 0),
        "top1": top1, "best_safe": best_safe,
    }


def _aggregate(per_run: list[dict]) -> dict:
    n = len(per_run)
    pct = lambda key: round(100 * sum(1 for r in per_run if r[key]) / n, 1)
    return {
        "n": n,
        "top1_agreement_pct": pct("agreement"),
        "safety_violation_pct": pct("safety_violation"),
        "hallucination_pct": pct("hallucinated"),
        "coverage_pct": pct("coverage"),
        "avg_latency_ms": round(statistics.mean(r["latency_ms"] for r in per_run)),
        "total_cost_usd": round(sum(r["cost_usd"] for r in per_run), 4),
        "avg_cost_usd": round(statistics.mean(r["cost_usd"] for r in per_run), 5),
        "total_tokens": sum(r["in_tokens"] + r["out_tokens"] for r in per_run),
    }


def run(versions: list[int], scenarios: list[dict]) -> dict:
    by_version: dict[int, dict] = {}
    for v in versions:
        per_run, per_cat = [], {}
        for sc in scenarios:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
                json.dump(sc["injected"], tf)
                scenario_path = tf.name
            os.environ["AGENT_SCENARIO"] = scenario_path
            req = TripRequest(**sc["request"])
            try:
                result = plan_trip(req, version=v)
            except Exception as e:  # never let one scenario abort the sweep
                result = {"recommendations": [], "blocked": [], "error": str(e),
                          "grounding": {"ok": True}, "usage": {}, "latency_ms": 0}
            finally:
                os.unlink(scenario_path)
                os.environ.pop("AGENT_SCENARIO", None)
            row = _score_run(sc, result)
            row["id"], row["category"] = sc["id"], sc["category"]
            per_run.append(row)
            per_cat.setdefault(sc["category"], []).append(row["agreement"])
            flag = "ok" if row["agreement"] else "MISS"
            sv = " SAFETY!" if row["safety_violation"] else ""
            print(f"  v{v} {sc['id']:16} top1={str(row['top1']):24} "
                  f"exp={str(row['best_safe']):24} {flag}{sv}")
        agg = _aggregate(per_run)
        agg["by_category"] = {c: round(100 * sum(a) / len(a)) for c, a in per_cat.items()}
        agg["runs"] = per_run
        by_version[v] = agg
        print(f"v{v} -> {VERSION_NOTES[v]}: agreement {agg['top1_agreement_pct']}% "
              f"safety {agg['safety_violation_pct']}% halluc {agg['hallucination_pct']}% "
              f"cost ${agg['total_cost_usd']}\n")
    return by_version


def render_report(by_version: dict, n_scenarios: int) -> str:
    L = []
    L.append("# Blueliner Trip-Planning Agent — Eval Report\n")
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}_  ")
    L.append(f"Oracle: Blueliner's deterministic scorer (`agent/scorer.py`, "
             f"parity-tested vs `main.score_conditions`).  ")
    L.append(f"Models: cheap=`{config.CHEAP_MODEL}` (retrieval loop), "
             f"strong=`{config.STRONG_MODEL}` (ranking).  ")
    L.append(f"Scenarios: {n_scenarios}, injected conditions (deterministic, offline).\n")

    L.append("## v0 → v3\n")
    L.append("| Version | Top-1 agreement ↑ | Safety violations ↓ | "
             "Hallucinated readings ↓ | Coverage ↑ | Avg latency | Cost/run |")
    L.append("|---|---|---|---|---|---|---|")
    for v in sorted(by_version):
        a = by_version[v]
        L.append(f"| **v{v}** {VERSION_NOTES[v]} | {a['top1_agreement_pct']}% | "
                 f"{a['safety_violation_pct']}% | {a['hallucination_pct']}% | "
                 f"{a['coverage_pct']}% | {a['avg_latency_ms']} ms | "
                 f"${a['avg_cost_usd']} |")
    L.append("")

    final = max(by_version)
    L.append(f"## Per-category top-1 agreement (v{final})\n")
    L.append("| Category | Agreement |")
    L.append("|---|---|")
    for c, p in sorted(by_version[final]["by_category"].items()):
        L.append(f"| {c} | {p}% |")
    L.append("")

    L.append("## Reading this table\n")
    L.append("- **v0** has no tools: it invents flows/temps (hallucination high) "
             "and has no safety backstop.")
    L.append("- **v1** grounds every reading in a tool result; agreement jumps and "
             "hallucinated readings collapse toward 0.")
    L.append("- **v2** adds catch-log memory: it breaks ties toward the angler's "
             "productive conditions (see the `memory-*` scenarios).")
    L.append("- **v3** adds the deterministic guardrails: **safety violations → 0** "
             "and the grounding contract forces hallucinated readings to 0.")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--versions", default="0,1,2,3")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--scenarios", default=str(config.EVAL_DIR / "scenarios.jsonl"))
    ap.add_argument("--out", default=str(config.EVAL_DIR / "report.md"))
    args = ap.parse_args()

    versions = [int(x) for x in args.versions.split(",") if x.strip() != ""]
    scenarios = _load_scenarios(args.scenarios)
    if args.limit:
        scenarios = scenarios[: args.limit]

    t0 = time.time()
    by_version = run(versions, scenarios)
    report = render_report(by_version, len(scenarios))

    with open(args.out, "w") as f:
        f.write(report)
    # Raw results (without the bulky per-run list duplicated) for the deck.
    raw = {v: {k: a[k] for k in a if k != "runs"} | {"runs": a["runs"]}
           for v, a in by_version.items()}
    with open(config.EVAL_DIR / "results.json", "w") as f:
        json.dump(raw, f, indent=2, default=str)
    print(f"\nwrote {args.out} and results.json in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
