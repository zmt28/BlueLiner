#!/usr/bin/env python3
"""Decide whether a SCHEDULED data-build needs to run, or can cheap-skip.

The daily seed-warming cron exists only to keep the trout-seed cache complete
and non-stale between manual publishes: it banks a seed for any source that
lacks one (e.g. a newly added registry source) and forces a refresh once
captures age past a threshold. Once every source has a seed younger than
SEED_MAX_AGE_DAYS, the cron can skip the ~30-minute build entirely.

Prints a status table and writes ``need_build=true|false`` to ``GITHUB_OUTPUT``.
The workflow gates the heavy steps on this output **for scheduled runs only** --
manual `workflow_dispatch` runs always build (a publish must rebuild). Exits 0
regardless; if anything goes wrong it errs toward building (need_build=true).

Stdlib only, so the cheap-skip decision happens before the geopandas/tippecanoe
installs -- a skipped cron run does no real work.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEDS_DIR = os.path.join(ROOT, "data", "nhdplus", "seeds")
SOURCES = os.path.join(ROOT, "data", "trout", "sources.json")
# A source is "stale" past this age; one stale seed triggers a full refresh
# (the build re-banks every reachable source). Publishes also rebuild, so this
# is just the no-publish safety net.
MAX_AGE_DAYS = int(os.environ.get("SEED_MAX_AGE_DAYS", "45"))


def seed_slug(source: dict) -> str:
    """MUST stay identical to build_clickable_streams.seed_slug
    (tests/test_seed_status.py asserts they match for every real source)."""
    raw = f"{source['state']} {source.get('label', '')}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def find_seed(source: dict) -> str | None:
    """Path of this source's seed (auto capture, else the legacy `seed:`
    file), or None if it has none yet."""
    auto = os.path.join(SEEDS_DIR, f"{seed_slug(source)}.json")
    if os.path.exists(auto):
        return auto
    legacy = source.get("seed")
    if legacy:
        path = os.path.join(ROOT, legacy)
        if os.path.exists(path):
            return path
    return None


def seed_age_days(path: str):
    """Age of a seed from its `captured_at`, or None when absent/unparseable
    (e.g. the legacy MD COMID list, which is static reference data and never
    counts as stale)."""
    try:
        with open(path, encoding="utf-8") as f:
            cap = json.load(f).get("captured_at")
        if not cap:
            return None
        dt = datetime.fromisoformat(str(cap).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def evaluate(sources: list[dict]) -> dict:
    missing: list[str] = []
    stale: list[str] = []
    fresh = 0
    for s in sources:
        label = s.get("label", s["state"])
        path = find_seed(s)
        if not path:
            missing.append(label)
            continue
        age = seed_age_days(path)
        if age is not None and age > MAX_AGE_DAYS:
            stale.append(f"{label} ({age}d)")
        else:
            fresh += 1
    return {"missing": missing, "stale": stale, "fresh": fresh,
            "total": len(sources)}


def main() -> int:
    try:
        sources = json.load(open(SOURCES))["sources"]
    except Exception as e:  # err toward building rather than skip-on-error
        print(f"could not read sources ({e}); forcing build")
        _emit(True)
        return 0
    r = evaluate(sources)
    need = bool(r["missing"] or r["stale"])
    print(f"seed status: {r['fresh']}/{r['total']} fresh, "
          f"{len(r['missing'])} missing, {len(r['stale'])} stale "
          f"(max age {MAX_AGE_DAYS}d)")
    if r["missing"]:
        print("  missing:", ", ".join(r["missing"]))
    if r["stale"]:
        print("  stale:  ", ", ".join(r["stale"]))
    print("  -> build needed" if need else "  -> all fresh; cron can skip")
    _emit(need)
    return 0


def _emit(need: bool) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"need_build={'true' if need else 'false'}\n")


if __name__ == "__main__":
    sys.exit(main())
