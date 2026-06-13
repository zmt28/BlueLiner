#!/usr/bin/env python3
"""Standing endpoint watcher for the flaky state-GIS servers we're waiting on.

[NETWORK -- meant to run on a schedule in the Actions endpoint-watch job; the
Claude Code sandbox egress allowlist blocks most state GIS hosts, so a LOCAL run
will show most entries DOWN. That is expected and is NOT an error -- the watcher
reports "down" and keeps going; it never crashes on an unreachable host.]

What it watches (three sources, all merged into one run):
  1. data/watch/watchlist.json   -- INVESTIGATION endpoints (field_dump / discover
                                     / verify). One-time captures we want the moment
                                     a retired/down server recovers.
  2. data/stocking/candidates.json    -- unverified stocking-feed leads, auto-added
  3. data/access_points/candidates.json -- unverified access-feed leads, auto-added
     ...the candidate leads are folded in as `verify`-kind entries.

Per-entry kinds:
  * field_dump -- probe a layer; if up, capture name/geometry/fields + 3 sample
                  features. If `field` is set, also query that field's distinct
                  values (returnDistinctValues=true).
  * discover   -- if up, enumerate the folder's services / search the AGOL org and
                  list trout/fish-named layers with record counts.
  * verify     -- run the candidate 4-check (meta, count, f=geojson sample,
                  in-state bbox). A candidate that PASSES is flagged
                  "READY TO PROMOTE" (the watcher does NOT edit sources.json --
                  promotion stays human-reviewed).

Output: a markdown report to stdout, to gis_verify_out/WATCH.md, and (when
GITHUB_STEP_SUMMARY is set) to the Actions step summary. It leads with a status
table (id | state | kind | UP/DOWN | captured?) so a recovery is obvious at a
glance, then per-entry detail for the reachable ones.

Always exits 0 -- it's a watcher, not a gate.

    python scripts/endpoint_watch.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, Optional

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    # (lat_min, lat_max, lon_min, lon_max) for all 50 states + DC.
    from states import STATE_BBOX as _STATE_BBOX
except Exception:  # pragma: no cover
    _STATE_BBOX = {}

UA = {"User-Agent": "Blueliner-watch/1.0 (+https://blueliner.app)"}
TIMEOUT = 15.0
RETRIES = 2          # total attempts past the first = flap tolerance
MAX_DISTINCT = 60
WATCHLIST = os.path.join(ROOT, "data", "watch", "watchlist.json")
CANDIDATE_FILES = [
    os.path.join(ROOT, "data", "stocking", "candidates.json"),
    os.path.join(ROOT, "data", "access_points", "candidates.json"),
]
OUT_FILE = os.path.join(ROOT, "gis_verify_out", "WATCH.md")

FISH_KW = ("fish", "trout", "stock", "plant", "access", "ramp", "boat",
           "angl", "launch", "lct", "cutthroat")


# --------------------------------------------------------------------------
# HTTP -- bounded, flap-tolerant. Returns a dict (json) or None (unreachable).
# A {"_http": code} marker means "host answered but with a client error".
# --------------------------------------------------------------------------
def _get(client: httpx.Client, url: str, params: Optional[dict] = None) -> Optional[dict]:
    for attempt in range(RETRIES + 1):
        try:
            r = client.get(url, params=params or {})
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code < 500:
                return {"_http": r.status_code}
        except httpx.HTTPError:
            pass
        if attempt < RETRIES:
            time.sleep(attempt + 1)
    return None


def _reachable(meta: Optional[dict]) -> bool:
    """A layer/folder is 'up' when we got real, usable JSON back: not None, not
    an HTTP client-error marker, not an ArcGIS {"error": ...} body, and not an
    ArcGIS server-error envelope {"status":"error", "messages":[...]} (some
    self-hosted servers -- e.g. MD geodata -- return HTTP 200 with that body
    while the backend map machines are unavailable; that must count as DOWN)."""
    if not meta or "_http" in meta or "error" in meta:
        return False
    if meta.get("status") == "error":
        return False
    return True


def _first_coord(geom: Optional[dict]):
    if not geom:
        return None
    c = geom.get("coordinates")
    while isinstance(c, (list, tuple)) and c and isinstance(c[0], (list, tuple)):
        c = c[0]
    return c if isinstance(c, (list, tuple)) and len(c) >= 2 else None


def _in_bbox(state: Optional[str], lon: float, lat: float) -> bool:
    if state in _STATE_BBOX:
        la0, la1, lo0, lo1 = _STATE_BBOX[state]
        return lo0 <= lon <= lo1 and la0 <= lat <= la1
    return True  # unknown state code -> skip the in-state check


def _layer_base(url: str) -> tuple[str, str]:
    """Split a (possibly ?where=...-carrying) candidate URL into (layer_url, where)."""
    base = url.split("/query", 1)[0].split("?", 1)[0].rstrip("/")
    where = "1=1"
    if "?" in url:
        from urllib.parse import parse_qs, urlsplit
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        where = q.get("where", "1=1")
    return base, where


# --------------------------------------------------------------------------
# Captures -- each returns (up: bool, lines: list[str], extra: dict).
# `extra` carries machine flags (e.g. ready_to_promote) for the status table.
# --------------------------------------------------------------------------
def capture_field_dump(client: httpx.Client, entry: dict) -> tuple[bool, list[str], dict]:
    base, where = _layer_base(entry["url"])
    meta = _get(client, base, {"f": "json"})
    if not _reachable(meta):
        return False, [], {}
    lines: list[str] = []
    name = meta.get("name")
    geom = meta.get("geometryType")
    fields = meta.get("fields") or []
    cnt = _get(client, base + "/query",
               {"where": where, "returnCountOnly": "true", "f": "json"})
    count = (cnt or {}).get("count")
    lines.append(f"- **layer:** `{name}`  geometry: `{geom}`  count: `{count}`")
    lines.append("- **fields** (name | type | alias | coded-domain):")
    for f in fields:
        coded = (f.get("domain") or {}).get("codedValues")
        cs = ("; ".join(f"{cv.get('code')}->{cv.get('name')}"
                        for cv in coded[:12]) if coded else "")
        lines.append(f"  - `{f.get('name')}` | "
                     f"{str(f.get('type', '')).replace('esriFieldType', '')} | "
                     f"{f.get('alias')!r}{(' | ' + cs) if cs else ''}")
    gj = _get(client, base + "/query", {
        "where": where, "outFields": "*", "f": "geojson",
        "resultRecordCount": "3", "outSR": "4326"})
    feats = (gj or {}).get("features") if isinstance(gj, dict) else None
    if feats:
        lines.append("- **sample features (3):**")
        for f in feats[:3]:
            props = f.get("properties") or {}
            lines.append("  - ```json")
            lines.append("    " + json.dumps(props, default=str)[:1400])
            lines.append("    ```")
    else:
        lines.append("- _f=geojson sample returned no features (layer up but "
                     "geojson may be unsupported)_")
    field = entry.get("field")
    if field:
        d = _get(client, base + "/query", {
            "where": where, "outFields": field, "returnGeometry": "false",
            "returnDistinctValues": "true", "f": "json"})
        vals = sorted({a.get("attributes", {}).get(field)
                       for a in (d or {}).get("features", [])
                       if a.get("attributes", {}).get(field) not in (None, "")},
                      key=str)
        lines.append(f"- **distinct `{field}`** "
                     f"({len(vals)} value(s)"
                     f"{', truncated' if len(vals) > MAX_DISTINCT else ''}): "
                     f"`{json.dumps(vals[:MAX_DISTINCT], default=str)}`")
    return True, lines, {}


def capture_discover(client: httpx.Client, entry: dict) -> tuple[bool, list[str], dict]:
    root = entry["url"].rstrip("/")
    data = _get(client, root, {"f": "json"})
    matches: list[str] = []

    if _reachable(data) and ("services" in data or "folders" in data):
        # ArcGIS Server folder / catalog root.
        for fld in data.get("folders", []):
            matches.append(f"  - _folder_ `{fld}`")
        for svc in data.get("services", []):
            sname, styp = svc.get("name", ""), svc.get("type", "")
            if styp in ("MapServer", "FeatureServer"):
                if any(k in sname.lower() for k in FISH_KW):
                    matches.append(f"  - `{sname}` ({styp})")
        if not matches:
            matches.append("  - _(reachable, no fish/trout-named services found)_")
        return True, ["- **services / layers matching fish/trout:**"] + matches, {}

    # Maybe a layer root with sub-layers, or an AGOL org search fallback.
    if _reachable(data) and data.get("layers"):
        for lyr in data.get("layers", []):
            lname = lyr.get("name") or ""
            if any(k in lname.lower() for k in FISH_KW):
                matches.append(f"  - layer {lyr.get('id')}: `{lname}` "
                               f"({lyr.get('geometryType') or 'table'})")
        return True, ["- **matching layers:**"] + (matches or
                      ["  - _(reachable, no fish/trout layers)_"]), {}

    # Org root that didn't answer the folder shape -> try an AGOL org search.
    sr = _get(client, "https://www.arcgis.com/sharing/rest/search", {
        "q": f"trout OR fish OR cutthroat owner:{entry.get('state', '')}",
        "f": "json", "num": "15",
        "filter": '(type:"Feature Service" OR type:"Map Service")'})
    if _reachable(sr) and sr.get("results") is not None:
        for item in sr.get("results", []):
            title = item.get("title", "")
            if any(k in title.lower() for k in FISH_KW):
                matches.append(f"  - `{title}` owner={item.get('owner')} "
                               f"-> {item.get('url')}")
        return True, ["- **AGOL search matches:**"] + (matches or
                      ["  - _(reachable, no fish/trout items)_"]), {}
    return False, [], {}


def capture_verify(client: httpx.Client, entry: dict) -> tuple[bool, list[str], dict]:
    base, where = _layer_base(entry["url"])
    state = entry.get("state")
    meta = _get(client, base, {"f": "json"})
    if not _reachable(meta):
        return False, [], {}
    fails: list[str] = []
    name = meta.get("name")
    geom = meta.get("geometryType")
    if not geom:
        fails.append("no geometryType")
    cnt = _get(client, base + "/query",
               {"where": where, "returnCountOnly": "true", "f": "json"})
    count = (cnt or {}).get("count") if _reachable(cnt) else None
    if count is None:
        fails.append("count query failed")
    elif count <= 0:
        fails.append("layer empty (count=0)")
    gj = _get(client, base + "/query", {
        "where": where, "outFields": "*", "f": "geojson",
        "resultRecordCount": "5", "outSR": "4326"})
    feats = (gj or {}).get("features") if isinstance(gj, dict) else None
    if not feats:
        fails.append("f=geojson returned no features")
    else:
        for f in feats:
            xy = _first_coord(f.get("geometry"))
            if xy and not _in_bbox(state, xy[0], xy[1]):
                fails.append(f"sample centroid ({xy[1]:.3f},{xy[0]:.3f}) "
                             f"outside {state} bbox")
                break
    fieldnames = [f.get("name") for f in (meta.get("fields") or [])]
    passed = not fails
    verdict = "PASS" if passed else "FAIL"
    lines = [f"- **verdict:** {verdict}  (`{name}`, geom `{geom}`, count `{count}`)"]
    if fails:
        lines.append("- **failures:** " + "; ".join(fails))
    lines.append(f"- **fields:** `{json.dumps(fieldnames[:30])}`")
    extra: dict = {}
    if passed and entry.get("_is_candidate"):
        lines.insert(0, "- :rotating_light: **READY TO PROMOTE** -- candidate "
                        "passes the 4-check. Review + add to sources.json "
                        "(human-gated; the watcher does not auto-edit).")
        extra["ready_to_promote"] = True
    return True, lines, extra


CAPTURES: dict[str, Callable[[httpx.Client, dict], tuple[bool, list[str], dict]]] = {
    "field_dump": capture_field_dump,
    "discover": capture_discover,
    "verify": capture_verify,
}


# --------------------------------------------------------------------------
# Entry loading
# --------------------------------------------------------------------------
def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def load_entries() -> tuple[list[dict], list[str]]:
    """Return (entries, warnings). Each entry has id/kind/state/url(+field/note).
    Candidate leads are folded in as verify-kind entries flagged _is_candidate."""
    entries: list[dict] = []
    warnings: list[str] = []

    wl = _load_json(WATCHLIST)
    if wl is None:
        warnings.append(f"watchlist missing or malformed: {WATCHLIST}")
    else:
        for e in wl.get("entries", []):
            if not e.get("id") or not e.get("url") or e.get("kind") not in CAPTURES:
                warnings.append(f"skipping malformed watchlist entry: "
                                f"{json.dumps(e)[:120]}")
                continue
            entries.append(dict(e))

    for path in CANDIDATE_FILES:
        domain = os.path.basename(os.path.dirname(path))
        cf = _load_json(path)
        if cf is None:
            warnings.append(f"candidates missing or malformed: {path}")
            continue
        for src in cf.get("sources", []):
            if not src.get("url"):
                continue
            st = src.get("state", "")
            label = src.get("label", "")
            slug = (label or src["url"]).lower()
            slug = "".join(ch if ch.isalnum() else "-" for ch in slug).strip("-")[:48]
            entries.append({
                "id": f"cand-{domain}-{st}-{slug}".lower(),
                "kind": "verify",
                "state": st,
                "url": src["url"],
                "note": f"[{domain} candidate] {label}",
                "_is_candidate": True,
            })
    return entries, warnings


# --------------------------------------------------------------------------
# Report assembly
# --------------------------------------------------------------------------
def build_report(results: list[dict], warnings: list[str]) -> str:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    up = sum(1 for r in results if r["up"])
    promo = sum(1 for r in results if r.get("extra", {}).get("ready_to_promote"))
    out: list[str] = []
    out.append("# Endpoint watch")
    out.append("")
    out.append(f"_Run: {ts} -- {up}/{len(results)} reachable"
               + (f", **{promo} READY TO PROMOTE**" if promo else "") + "._")
    out.append("")
    if warnings:
        out.append("> Warnings: " + "; ".join(warnings))
        out.append("")

    # Status table -- recovery is obvious at a glance.
    out.append("| id | state | kind | status | captured |")
    out.append("|----|-------|------|--------|----------|")
    for r in results:
        status = "UP" if r["up"] else "DOWN"
        captured = "yes" if r["up"] else "-"
        if r.get("extra", {}).get("ready_to_promote"):
            captured = "PROMOTE"
        out.append(f"| {r['id']} | {r.get('state', '')} | {r['kind']} "
                   f"| {status} | {captured} |")
    out.append("")

    # Per-entry detail for the reachable ones.
    detailed = [r for r in results if r["up"]]
    if detailed:
        out.append("## Captured detail (reachable entries)")
        out.append("")
        for r in detailed:
            out.append(f"### {r['id']} ({r.get('state', '')} / {r['kind']})")
            if r.get("note"):
                out.append(f"> {r['note']}")
            out.append("")
            out.extend(r["lines"])
            out.append("")
    else:
        out.append("_No watched endpoints were reachable this run. "
                   "(In the Claude Code sandbox this is expected -- egress "
                   "blocks most state GIS hosts; the scheduled Actions job has "
                   "open egress.)_")
        out.append("")
    return "\n".join(out)


def run(client: httpx.Client, entries: list[dict]) -> list[dict]:
    results: list[dict] = []
    for e in entries:
        capture = CAPTURES[e["kind"]]
        try:
            up, lines, extra = capture(client, e)
        except Exception as exc:  # a watcher never crashes on one bad host
            up, lines, extra = False, [], {}
            e = dict(e, note=(e.get("note", "") +
                              f"  (probe error: {type(exc).__name__})"))
        results.append({
            "id": e["id"], "state": e.get("state", ""), "kind": e["kind"],
            "note": e.get("note", ""), "up": up, "lines": lines, "extra": extra,
        })
    return results


def main() -> int:
    entries, warnings = load_entries()
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        results = run(client, entries)
    report = build_report(results, warnings)

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w") as fh:
        fh.write(report + "\n")
    print(report)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        try:
            with open(summary, "a") as fh:
                fh.write(report + "\n")
        except OSError:
            pass
    return 0  # always: it's a watcher, not a gate


if __name__ == "__main__":
    raise SystemExit(main())
