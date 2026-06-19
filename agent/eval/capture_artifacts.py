"""Capture the deck-evidence artifacts deterministically (injected scenarios).

Produces, under agent/eval/:
  sample_trace.json / sample_trace.md  -- one v3 run where the access guardrail
      vetoes a private-water river and the agent recommends the public one,
      fully grounded (tools chosen, the veto firing, the final answer).
  sample_alert.txt  -- a proactive not-ideal -> ideal alert (dev-rendered email).

The live-USGS CLI plan is captured separately (it must hit the network, not
injected conditions) -- see README.

Run:  python -m agent.eval.capture_artifacts
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile

from agent import config, watch
from agent.agent import TripRequest, plan_trip


def _scenario_file(d: dict) -> str:
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(d, f)
    f.close()
    return f.name


def _last_trace() -> dict:
    lines = (config.LOG_DIR / "runs.jsonl").read_text().strip().splitlines()
    return json.loads(lines[-1])


def capture_trace() -> None:
    # Beaver Creek is in great shape but PRIVATE; Gunpowder is public & green.
    inj = {
        "candidates": ["beaver-creek-md", "gunpowder-falls-md"],
        "conditions": {
            "beaver-creek-md": {"water_temp_f": 55, "flow_cfs": 22, "median_cfs": 24,
                                "last_updated_hours_ago": 0.5, "access_tier": "private"},
            "gunpowder-falls-md": {"water_temp_f": 56, "flow_cfs": 120, "median_cfs": 110,
                                   "last_updated_hours_ago": 0.5, "access_tier": "public"},
        },
    }
    os.environ["AGENT_SCENARIO"] = _scenario_file(inj)
    try:
        req = TripRequest(
            lat=39.60, lng=-77.70, radius_miles=120, top_n=3, user_id=1,
            text="Heard Beaver Creek is fishing great near Hagerstown -- worth it this weekend?")
        res = plan_trip(req, version=3)
        trace = _last_trace()
    finally:
        os.environ.pop("AGENT_SCENARIO", None)

    json.dump(trace, open(config.EVAL_DIR / "sample_trace.json", "w"),
              indent=2, default=str)
    (config.EVAL_DIR / "sample_trace.md").write_text(_render_trace_md(trace, res))
    blocked = [b["river_id"] for b in res.get("blocked", [])]
    print(f"wrote sample_trace.json + sample_trace.md "
          f"(top={[r['river_id'] for r in res['recommendations']]}, blocked={blocked})")


def _render_trace_md(trace: dict, res: dict) -> str:
    L = ["# Sample agent trace (v3) — access guardrail veto\n"]
    L.append(f"**Request:** {trace['request'].get('text')}  ")
    L.append(f"**Models:** retrieval=`{trace.get('cheap_model')}`, "
             f"ranking=`{trace.get('strong_model')}`  ")
    L.append(f"**Latency:** {trace.get('latency_ms')} ms · "
             f"**Cost:** ${trace.get('usage', {}).get('est_cost_usd')} · "
             f"**Tokens:** {trace.get('usage', {}).get('input_tokens')} in / "
             f"{trace.get('usage', {}).get('output_tokens')} out\n")
    L.append("## Tools the agent chose (in order)\n")
    L.append("| # | tool | args | latency | source |")
    L.append("|---|---|---|---|---|")
    for i, t in enumerate(trace.get("tool_calls", []), 1):
        args = ", ".join(f"{k}={v}" for k, v in t["args"].items())
        L.append(f"| {i} | `{t['name']}` | {args} | {t['latency_ms']} ms | "
                 f"{t.get('source') or ''} |")
    L.append("")
    if res.get("blocked"):
        L.append("## Guardrail veto\n")
        for b in res["blocked"]:
            L.append(f"- ❌ **{b['river_id']}** — {b['reason']}")
        L.append("")
    if res.get("violations"):
        L.append("## Violations logged\n")
        for v in res["violations"]:
            L.append(f"- `{v['rule']}` ({v.get('river_id') or '-'}): {v['detail']}")
        L.append("")
    L.append("## Recommendation (grounded)\n")
    for r in res.get("recommendations", []):
        L.append(f"### {r.get('name')} — {r.get('overall_score')} "
                 f"(confidence: {r.get('confidence')})")
        L.append(f"> {r.get('verdict')}\n")
        for w in r.get("why", []):
            L.append(f"- {w}")
        if r.get("sources"):
            L.append(f"\n_sources: {', '.join(r['sources'])}_")
        L.append("")
    g = res.get("grounding", {})
    L.append(f"**Grounding check:** ok={g.get('ok')} · unsourced={g.get('unsourced')}")
    return "\n".join(L)


def capture_alert() -> None:
    # The three watched rivers all green/public/fresh -> ideal; --demo forces the
    # not-ideal -> ideal transition so the alert fires for the screenshot.
    inj = {
        "candidates": list(watch.DEFAULT_WATCHLIST["demo-angler@blueliner.app"]),
        "conditions": {
            "north-branch-potomac-md": {"water_temp_f": 56, "flow_cfs": 240, "median_cfs": 260,
                                        "last_updated_hours_ago": 0.4, "access_tier": "public"},
            "savage-river-md": {"water_temp_f": 52, "flow_cfs": 120, "median_cfs": 130,
                                "last_updated_hours_ago": 0.5, "access_tier": "public"},
            "gunpowder-falls-md": {"water_temp_f": 55, "flow_cfs": 100, "median_cfs": 110,
                                   "last_updated_hours_ago": 0.6, "access_tier": "public"},
        },
    }
    os.environ["AGENT_SCENARIO"] = _scenario_file(inj)
    (config.LOG_DIR / "watch_state.json").unlink(missing_ok=True)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            watch.tick(force_transition=True)
    finally:
        os.environ.pop("AGENT_SCENARIO", None)
    out = buf.getvalue()
    (config.EVAL_DIR / "sample_alert.txt").write_text(out)
    print("wrote sample_alert.txt")
    print(out)


def main():
    capture_trace()
    capture_alert()


if __name__ == "__main__":
    main()
