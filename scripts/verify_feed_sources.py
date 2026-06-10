#!/usr/bin/env python3
"""
Verify the live stocking / access-point feed registries end-to-end.

For every source in data/stocking/sources.json and
data/access_points/sources.json (plus, with --candidates, the
unpromoted leads in data/*/candidates.json), run the same checks used
when an entry is first added:

  1. layer metadata  -- `<layer>?f=json` resolves, has a geometryType
  2. record count    -- `/query?returnCountOnly=true` > 0
  3. GeoJSON sample  -- `/query?f=geojson&resultRecordCount=5` returns
                        real features (the runtime fetcher needs
                        f=geojson support)
  4. geometry sanity -- sample centroids fall inside the source
                        state's bbox
  5. field mapping   -- name_field / species_field / species_flags /
                        type_field / notes_field exist in the layer

Exits nonzero if any *registry* source fails (candidates only warn).
Run this where outbound network is open -- the GitHub Actions runners
(data-build.yml) or a dev machine. The Claude Code sandbox allowlists
only a few state hosts, so candidate verification usually can't happen
there; this script is how a candidate gets promoted into sources.json.

Usage:
    python scripts/verify_feed_sources.py               # registry only
    python scripts/verify_feed_sources.py --candidates  # also lint leads
"""

import json
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from states import STATE_BBOX  # noqa: E402

TIMEOUT = 30.0
UA = {"User-Agent": "Blueliner/1.0 (+https://blueliner.app)"}

DOMAINS = ("stocking", "access_points")


def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    """GET with 3 attempts + backoff. Some agency hosts (DE firstmap)
    are chronically slow and time out on the first hit but answer on
    retry; the runtime fetcher degrades gracefully, but the CI gate
    shouldn't go red on a single slow response."""
    import time
    last: Exception | None = None
    for attempt in range(3):
        try:
            return client.get(url, params=params).json()
        except Exception as exc:
            last = exc
            time.sleep(2 * (attempt + 1))
    raise last


def _layer_url(query_url: str) -> str:
    return query_url.rsplit("/query", 1)[0]


def _centroid(geom: dict):
    """Rough centroid: mean of all coordinates. Good enough for a
    'is it inside the state bbox' sanity check."""
    coords: list[tuple[float, float]] = []

    def walk(c):
        if isinstance(c, (list, tuple)):
            if len(c) >= 2 and all(isinstance(x, (int, float)) for x in c[:2]):
                coords.append((float(c[0]), float(c[1])))
            else:
                for item in c:
                    walk(item)

    walk(geom.get("coordinates"))
    if not coords:
        return None
    return (sum(x for x, _ in coords) / len(coords),
            sum(y for _, y in coords) / len(coords))


def verify_source(client: httpx.Client, src: dict) -> list[str]:
    """Return a list of failure strings ([] = source passed)."""
    fails: list[str] = []
    url = src["url"]
    layer = _layer_url(url)
    state = src.get("state")

    try:
        meta = _get_json(client, layer, {"f": "json"})
    except Exception as exc:
        return [f"layer metadata fetch failed: {exc}"]
    if "error" in meta or not meta.get("geometryType"):
        return [f"layer metadata invalid: {json.dumps(meta)[:160]}"]

    field_names = {f["name"] for f in meta.get("fields", [])}
    declared = ([src.get(k) for k in ("name_field", "species_field",
                                      "type_field", "notes_field")]
                + list((src.get("species_flags") or {}).keys())
                + list((src.get("type_flags") or {}).keys()))
    for fld in declared:
        if fld and fld not in field_names:
            fails.append(f"declared field {fld!r} not in layer schema")

    base = url.split("?", 1)[0]
    where = "1=1"
    if "?" in url:
        from urllib.parse import parse_qs, urlsplit
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        where = q.get("where", "1=1")

    try:
        cnt = _get_json(client, base, {
            "where": where, "returnCountOnly": "true", "f": "json",
        }).get("count")
    except Exception as exc:
        cnt = None
        fails.append(f"count query failed: {exc}")
    if cnt is not None and cnt <= 0:
        fails.append("layer is empty (count=0)")

    try:
        gj = _get_json(client, base, {
            "where": where, "outFields": "*", "f": "geojson",
            "outSR": "4326", "resultRecordCount": "5",
        })
        feats = gj.get("features") or []
    except Exception as exc:
        return fails + [f"f=geojson sample failed: {exc}"]
    if not feats:
        return fails + ["f=geojson returned no features"]

    if state in STATE_BBOX:
        la0, la1, lo0, lo1 = STATE_BBOX[state]
        for f in feats:
            g = f.get("geometry")
            c = _centroid(g) if g else None
            if c and not (la0 <= c[1] <= la1 and lo0 <= c[0] <= lo1):
                fails.append(
                    f"sample centroid ({c[1]:.3f},{c[0]:.3f}) outside "
                    f"{state} bbox")
                break

    if cnt is not None:
        fails_label = ", ".join(fails) if fails else "ok"
        print(f"  [{src.get('state')}] {src.get('label')}: "
              f"{cnt} records, {meta.get('geometryType')} -- {fails_label}")
    return fails


def run(path: str, required: bool, client: httpx.Client) -> int:
    if not os.path.exists(path):
        return 0
    raw = json.load(open(path))
    n_failed = 0
    for src in raw.get("sources", []):
        fails = verify_source(client, src)
        if fails:
            n_failed += 1
            sev = "FAIL" if required else "warn"
            for f in fails:
                print(f"  {sev}: [{src.get('state')}] "
                      f"{src.get('label')}: {f}")
    return n_failed if required else 0


def main() -> int:
    check_candidates = "--candidates" in sys.argv
    failed = 0
    with httpx.Client(timeout=TIMEOUT, headers=UA) as client:
        for domain in DOMAINS:
            reg = os.path.join(ROOT, "data", domain, "sources.json")
            print(f"[verify] {domain} registry:")
            failed += run(reg, required=True, client=client)
            if check_candidates:
                cand = os.path.join(ROOT, "data", domain, "candidates.json")
                if os.path.exists(cand):
                    print(f"[verify] {domain} candidates "
                          f"(advisory -- promote passing entries):")
                    run(cand, required=False, client=client)
    if failed:
        print(f"\n[verify] {failed} registry source(s) FAILED")
        return 1
    print("\n[verify] all registry sources OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
