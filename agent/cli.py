"""Interactive entry point: plan a trip and pretty-print the result + trace.

    python -m agent.cli --lat 39.29 --lng -76.61 --radius 90 --version 3
    python -m agent.cli --lat 39.29 --lng -76.61 --user-id 1   # personalized
"""

from __future__ import annotations

import argparse
import json

from .agent import TripRequest, plan_trip


def _print(result: dict) -> None:
    print("\n=== Recommendations ===")
    for i, r in enumerate(result.get("recommendations", []), 1):
        print(f"{i}. {r.get('name')} [{r.get('overall_score')}] "
              f"(confidence: {r.get('confidence')})")
        print(f"   verdict: {r.get('verdict')}")
        for w in r.get("why", []):
            print(f"     - {w}")
        if r.get("warnings"):
            print(f"     ! {'; '.join(r['warnings'])}")
        if r.get("sources"):
            print(f"     sources: {', '.join(r['sources'])}")
    if result.get("blocked"):
        print("\n=== Blocked (guardrails) ===")
        for b in result["blocked"]:
            print(f"   x {b.get('river_id')}: {b.get('reason')}")
    if result.get("violations"):
        print("\n=== Guardrail violations (logged) ===")
        for v in result["violations"]:
            print(f"   [{v['rule']}] {v.get('river_id') or '-'}: {v['detail']}")
    g = result.get("grounding", {})
    print(f"\ngrounding ok: {g.get('ok')}  unsourced: {g.get('unsourced')}")
    u = result.get("usage", {})
    print(f"latency: {result.get('latency_ms')} ms   "
          f"tokens: {u.get('input_tokens')}in/{u.get('output_tokens')}out   "
          f"est cost: ${u.get('est_cost_usd')}")
    if result.get("notes"):
        print(f"notes: {result['notes']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lng", type=float, required=True)
    ap.add_argument("--state", default=None)
    ap.add_argument("--radius", type=int, default=90)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--user-id", type=int, default=None)
    ap.add_argument("--version", type=int, default=3)
    ap.add_argument("--dates", default=None)
    ap.add_argument("--prefs", default="")
    ap.add_argument("--text", default="Where should I fish?")
    ap.add_argument("--json", action="store_true", help="dump raw result JSON")
    args = ap.parse_args()

    req = TripRequest(lat=args.lat, lng=args.lng, state=args.state,
                      radius_miles=args.radius, top_n=args.top_n,
                      user_id=args.user_id, dates=args.dates,
                      preferences=args.prefs, text=args.text)
    result = plan_trip(req, version=args.version)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print(result)


if __name__ == "__main__":
    main()
