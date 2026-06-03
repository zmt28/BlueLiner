#!/usr/bin/env python3
"""Phase-0 trout-source discovery CLI.

Subcommands:
  eval                      Grade the classifier against the 10 shipped states
                            (OFFLINE -- no network; the spike's headline number).
  discover --states CO,MI   Crawl catalogs, probe candidates, classify, and emit
                            per-state dossiers + a go/no-go memo (NEEDS open
                            egress -- run in the GitHub Actions discovery job).

Run from the repo root:
  python scripts/discover_trout_sources.py eval
  python scripts/discover_trout_sources.py discover --states CO,MI,WI,TN --out discovery_out

`scripts/` is on sys.path[0] when invoked as a script, so `from discovery import ...`
resolves the sibling package.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow `python scripts/discover_trout_sources.py` AND `python -m`:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmd_eval(_args) -> int:
    from discovery import eval as gold_eval
    return gold_eval.main()


def cmd_discover(args) -> int:
    # Imported lazily: these modules need httpx + open egress, so `eval` works
    # in environments (like the locked-down sandbox) where they can't run.
    from discovery import catalogs, probe, classify, report

    states = [s.strip().upper() for s in args.states.split(",") if s.strip()]
    os.makedirs(args.out, exist_ok=True)
    dossiers = []
    for state in states:
        print(f"[{state}] discovering ...")
        candidates = catalogs.find_candidates(state, top_k=args.top_k)
        scored = [probe.probe(c) for c in candidates]
        scored = [s for s in scored if s is not None]
        dossier = report.build_dossier(state, scored, classify)
        report.write_dossier(dossier, args.out)
        dossiers.append(dossier)
        print(f"[{state}] tier {dossier['tier']} "
              f"({dossier.get('confidence', '?')})")
    report.write_memo(dossiers, args.out)
    print(f"\nWrote {len(dossiers)} dossiers + memo to {args.out}/")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("eval", help="offline classifier eval vs gold states") \
        .set_defaults(func=cmd_eval)

    d = sub.add_parser("discover", help="networked per-state discovery")
    d.add_argument("--states", required=True, help="comma-separated, e.g. CO,MI,WI,TN")
    d.add_argument("--top-k", type=int, default=8, help="candidates probed per state")
    d.add_argument("--out", default="discovery_out", help="output dir for dossiers")
    d.set_defaults(func=cmd_discover)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
