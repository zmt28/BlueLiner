#!/usr/bin/env python3
"""
Lint everything under data/. Run before committing edits.

Checks for each domain:

- data/stocking/<STATE>.json
    * Required fields per entry (water, lat, lon, species, category,
      season_months, agency_url).
    * lat/lon falls within STATE_BBOX (catches the "typo dropped a PA
      stream into the Atlantic" case).
    * season_months is [start, end] with both 1..12.
- data/hatches/overrides.json
    * Each river -> list of chart entries with the required fields
      (insect, common_name, months, peak, hook_sizes, time_of_day,
      patterns).
- data/trout/<STATE>.json (optional, GeoJSON FeatureCollection)
    * Loads as JSON and has the right type.

Exits nonzero on any error; prints a per-domain coverage summary.

Usage:  python scripts/validate_data.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from states import STATE_BBOX  # noqa: E402

_DATA = os.path.join(ROOT, "data")
errors: list[str] = []


def err(path: str, msg: str) -> None:
    errors.append(f"{path}: {msg}")


def _in_state(state: str, lat: float, lon: float) -> bool:
    if state not in STATE_BBOX:
        return True   # unknown state code is checked separately
    la0, la1, lo0, lo1 = STATE_BBOX[state]
    return la0 <= lat <= la1 and lo0 <= lon <= lo1


def validate_stocking() -> int:
    count = 0
    d = os.path.join(_DATA, "stocking")
    if not os.path.isdir(d):
        return 0
    required = {"water", "lat", "lon", "species", "category",
                "season_months", "agency_url"}
    for fn in sorted(os.listdir(d)):
        if (not fn.endswith(".json")
                or fn in ("sources.json", "candidates.json")):
            continue
        state = fn[:-5].upper()
        path = os.path.join(d, fn)
        try:
            rows = json.load(open(path))
        except Exception as exc:
            err(path, f"JSON load failed: {exc}")
            continue
        if not isinstance(rows, list):
            err(path, "top-level must be a list")
            continue
        for i, r in enumerate(rows):
            missing = required - set(r)
            if missing:
                err(path, f"entry {i}: missing {sorted(missing)}")
                continue
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError):
                err(path, f"entry {i}: non-numeric lat/lon")
                continue
            if not _in_state(state, lat, lon):
                err(path, f"entry {i} ({r['water']}): "
                          f"({lat},{lon}) outside {state} bbox")
            sm = r["season_months"]
            if (not isinstance(sm, list) or len(sm) != 2
                    or not all(isinstance(x, int) and 1 <= x <= 12 for x in sm)):
                err(path, f"entry {i}: season_months must be [1..12, 1..12]")
            count += 1
    return count


_ACCESS_TYPES = {"boat_ramp", "walk_in", "pier", "parking", "wading_access"}
_ACCESS_LEVELS = {"public", "permit", "fee", "private_easement"}


def validate_access_points() -> int:
    count = 0
    d = os.path.join(_DATA, "access_points")
    if not os.path.isdir(d):
        return 0
    required = {"name", "lat", "lon", "type", "access"}
    for fn in sorted(os.listdir(d)):
        if (not fn.endswith(".json")
                or fn in ("sources.json", "candidates.json")):
            continue
        state = fn[:-5].upper()
        path = os.path.join(d, fn)
        try:
            rows = json.load(open(path))
        except Exception as exc:
            err(path, f"JSON load failed: {exc}")
            continue
        if not isinstance(rows, list):
            err(path, "top-level must be a list")
            continue
        for i, r in enumerate(rows):
            missing = required - set(r)
            if missing:
                err(path, f"entry {i}: missing {sorted(missing)}")
                continue
            try:
                lat, lon = float(r["lat"]), float(r["lon"])
            except (TypeError, ValueError):
                err(path, f"entry {i}: non-numeric lat/lon")
                continue
            if not _in_state(state, lat, lon):
                err(path, f"entry {i} ({r['name']}): "
                          f"({lat},{lon}) outside {state} bbox")
            if r["type"] not in _ACCESS_TYPES:
                err(path, f"entry {i}: type {r['type']!r} not in "
                          f"{sorted(_ACCESS_TYPES)}")
            if r["access"] not in _ACCESS_LEVELS:
                err(path, f"entry {i}: access {r['access']!r} not in "
                          f"{sorted(_ACCESS_LEVELS)}")
            count += 1
    return count


def validate_live_registry(domain: str) -> int:
    """Lint data/<domain>/sources.json (live ArcGIS feed registry).
    Structural checks only -- endpoint liveness is verified when an
    entry is added, and the runtime loader degrades gracefully."""
    path = os.path.join(_DATA, domain, "sources.json")
    if not os.path.exists(path):
        return 0
    try:
        raw = json.load(open(path))
    except Exception as exc:
        err(path, f"JSON load failed: {exc}")
        return 0
    sources = raw.get("sources")
    if not isinstance(sources, list):
        err(path, "must have a top-level 'sources' list")
        return 0
    required = {"state", "label", "url", "agency_url"}
    for i, s in enumerate(sources):
        missing = required - set(s)
        if missing:
            err(path, f"source {i}: missing {sorted(missing)}")
            continue
        if s["state"] not in STATE_BBOX:
            err(path, f"source {i}: unknown state {s['state']!r}")
        if "/query" not in s["url"]:
            err(path, f"source {i} ({s['label']}): url must be an "
                      "ArcGIS /query endpoint")
        sm = s.get("season_months")
        if sm is not None and (
                not isinstance(sm, list) or len(sm) != 2
                or not all(isinstance(x, int) and 1 <= x <= 12 for x in sm)):
            err(path, f"source {i}: season_months must be [1..12, 1..12]")
    return len(sources)


def validate_hatch_overrides() -> int:
    path = os.path.join(_DATA, "hatches", "overrides.json")
    if not os.path.exists(path):
        return 0
    try:
        raw = json.load(open(path))
    except Exception as exc:
        err(path, f"JSON load failed: {exc}")
        return 0
    required = {"insect", "common_name", "months", "peak", "hook_sizes",
                "time_of_day", "patterns"}
    rivers = 0
    for key, entries in raw.items():
        if key.startswith("_"):
            continue
        if not isinstance(entries, list):
            err(path, f"{key}: value must be a list of chart entries")
            continue
        rivers += 1
        for i, e in enumerate(entries):
            missing = required - set(e)
            if missing:
                err(path, f"{key}[{i}]: missing {sorted(missing)}")
                continue
            for fld in ("months", "peak"):
                v = e[fld]
                if (not isinstance(v, list) or len(v) != 2
                        or not all(isinstance(x, int) and 1 <= x <= 12 for x in v)):
                    err(path, f"{key}[{i}].{fld}: must be [1..12, 1..12]")
            if not isinstance(e["patterns"], list) or not e["patterns"]:
                err(path, f"{key}[{i}].patterns: must be non-empty list")
    return rivers


def validate_trout() -> int:
    d = os.path.join(_DATA, "trout")
    if not os.path.isdir(d):
        return 0
    count = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        # sources.json is the declarative trout-source registry (build-time
        # endpoints/classification, validated by trout_registry), not a
        # per-state GeoJSON bundle -- skip it here.
        if fn == "sources.json":
            continue
        path = os.path.join(d, fn)
        try:
            data = json.load(open(path))
        except Exception as exc:
            err(path, f"JSON load failed: {exc}")
            continue
        if (not isinstance(data, dict)
                or data.get("type") != "FeatureCollection"):
            err(path, "must be a GeoJSON FeatureCollection")
            continue
        feats = data.get("features") or []
        if not isinstance(feats, list):
            err(path, "features must be a list")
            continue
        count += 1
    return count


def main() -> int:
    stocking_n = validate_stocking()
    access_n = validate_access_points()
    stocking_src_n = validate_live_registry("stocking")
    access_src_n = validate_live_registry("access_points")
    hatch_n = validate_hatch_overrides()
    trout_n = validate_trout()

    print(f"[validate] stocking entries: {stocking_n}")
    print(f"[validate] access entries:   {access_n}")
    print(f"[validate] stocking feeds:   {stocking_src_n}")
    print(f"[validate] access feeds:     {access_src_n}")
    print(f"[validate] hatch overrides:  {hatch_n} rivers")
    print(f"[validate] trout files:      {trout_n} states")

    if errors:
        print(f"\n[validate] {len(errors)} error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("[validate] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
