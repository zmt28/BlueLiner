#!/usr/bin/env python3
"""Build data/stocking/UT.json from Utah DWR's "Fish Utah" app.

UDWR has no ArcGIS stocking feed, but its Fish Utah web app
(https://dwrapps.utah.gov/fishing) exposes a small JSON API with coordinates,
species, and multi-year stocking history per water:

    /fishing/fSetup            -> {nameList, nameListId, speciesList, ...}
    /fishing/BySegment?SEGID=  -> {hsInfo{displayName, centerX=lon, centerY=lat,
                                   status}, hsSpecies, hsStocking, ...}

We enumerate the ~295 waters, keep those carrying a trout-family species
(present or stocked), and emit the stocking-overlay baseline shape consumed by
stocking.py: {water, lat, lon, species[], category, season_months, agency_url}.

This is a COMMITTED SNAPSHOT, not a live source (the loader's live feeds are
ArcGIS-only, and dwrapps is a bespoke app). Per the "holdovers" rationale a
recent snapshot is fine -- a water stocked last year still fishes as trout
water. Re-run to refresh.

[NETWORK -- run where dwrapps.utah.gov is reachable; the Claude Code sandbox can
reach it. Sequential-ish; ~295 small GETs.]

    python scripts/build_ut_stocking.py
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from states import STATE_BBOX  # noqa: E402

BASE = "https://dwrapps.utah.gov/fishing"
UA = {"User-Agent": "Blueliner/1.0 (+https://blueliner.app)"}
OUT = os.path.join(ROOT, "data", "stocking", "UT.json")

# Trout family (true trout + char + grayling) -> short popup label. Ordered so
# multi-word keys match before bare ones ("tiger trout" before "brown"/"brook").
# Kokanee, all whitefish/cisco, and every warmwater species are intentionally
# excluded -- this is a trout overlay.
TROUT_MAP = [
    ("tiger trout", "Tiger"),
    ("cutthroat", "Cutthroat"),
    ("rainbow", "Rainbow"),
    ("brown trout", "Brown"),
    ("brook trout", "Brook"),
    ("golden trout", "Golden"),
    ("lake trout", "Lake"),
    ("splake", "Splake"),
    ("arctic grayling", "Grayling"),
    ("grayling", "Grayling"),
]


def trout_label(species_name: str) -> str | None:
    n = (species_name or "").lower()
    for key, lab in TROUT_MAP:
        if key in n:
            return lab
    return None


def trout_species(seg: dict) -> list[str]:
    """Ordered, de-duped trout-family labels from a water's present species
    (hsSpecies) and its stocking history (hsStocking)."""
    names: list[str] = []
    for s in (seg.get("hsSpecies") or {}).get("hotsSpecies") or []:
        names.append(s.get("speciesName", ""))
    for s in (seg.get("hsStocking") or {}).get("hlStocking") or []:
        names.append(s.get("species", ""))
    out: list[str] = []
    for nm in names:
        lab = trout_label(nm)
        if lab and lab not in out:
            out.append(lab)
    return out


def fetch_segment(client: httpx.Client, sid: int, name: str):
    for attempt in range(3):
        try:
            r = client.get(f"{BASE}/BySegment", params={"SEGID": sid})
            r.raise_for_status()
            return sid, name, r.json()
        except Exception as e:  # transient -- retry a couple times
            if attempt == 2:
                print(f"  WARN seg {sid} ({name}): {e}")
                return sid, name, None
    return sid, name, None


def build() -> list[dict]:
    la0, la1, lo0, lo1 = STATE_BBOX["UT"]
    with httpx.Client(timeout=30.0, headers=UA, follow_redirects=True) as client:
        setup = client.get(f"{BASE}/fSetup")
        setup.raise_for_status()
        s = setup.json()
        waters = list(zip(s["nameListId"], s["nameList"]))
        print(f"{len(waters)} waters in fSetup; fetching per-water detail ...")
        with ThreadPoolExecutor(max_workers=8) as ex:
            segs = list(ex.map(lambda p: fetch_segment(client, *p), waters))

    rows: list[dict] = []
    dropped = {"no_trout": 0, "no_coord": 0, "out_of_bbox": 0, "no_data": 0}
    for sid, name, seg in segs:
        if not seg:
            dropped["no_data"] += 1
            continue
        info = seg.get("hsInfo") or {}
        species = trout_species(seg)
        if not species:
            dropped["no_trout"] += 1
            continue
        try:
            lon = float(info.get("centerX"))
            lat = float(info.get("centerY"))
        except (TypeError, ValueError):
            dropped["no_coord"] += 1
            continue
        if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
            dropped["out_of_bbox"] += 1
            continue
        water = (info.get("displayName") or name or "").strip()
        stocked = bool((seg.get("hsStocking") or {}).get("hlStocking"))
        category = ("Stocked trout water (UDWR)" if stocked
                    else "Managed trout water (UDWR)")
        rows.append({
            "water": water,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "species": species,
            "category": category,
            "season_months": [1, 12],
            "agency_url": f"{BASE}/?NA={quote(water)}",
        })

    rows.sort(key=lambda r: r["water"].lower())
    print(f"kept {len(rows)} trout waters; dropped {dropped}")
    return rows


def main() -> int:
    rows = build()
    if len(rows) < 50:
        print(f"ERROR: only {len(rows)} waters -- refusing to overwrite "
              f"(endpoint likely degraded)", file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    print(f"wrote {len(rows)} entries -> {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
