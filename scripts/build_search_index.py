#!/usr/bin/env python3
"""
Build the client-side search index (M4.2): every USGS stream gauge,
county, and town in the lower 48, as one compact JSON the client
fetches lazily on first search focus. This is what turns the search
box from a river-name filter into the "rivers, gauges, counties..."
its placeholder always promised -- with zero runtime search service.

Sources (all free/public-domain):
- Gauges:   USGS Site Service (waterservices.usgs.gov), siteType=ST,
            per state. Reachable from the Claude sandbox too (USGS is
            allowlisted), so this half is testable locally.
- Counties: Census Gazetteer national counties file. CI-only egress.
- Places:   Census Gazetteer national places file (towns/CDPs).
            CI-only egress.

Output shape (compact positional arrays keep ~40k entries small):

    {"v": 1,
     "gauges":   [[site_no, name, state, lat, lon], ...],
     "counties": [[name, state, lat, lon], ...],
     "places":   [[name, state, lat, lon], ...]}

Emitted as search_index.json.gz (the client decompresses via
DecompressionStream) plus a plain search_index.json fallback.

    python scripts/build_search_index.py --out data/search/search_index.json.gz
    python scripts/build_search_index.py --states MD,VA --skip-census  # local smoke
"""

import argparse
import gzip
import io
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from states import STATES  # noqa: E402

_UA = {"User-Agent": "blueliner-data-build (github.com/zmt28/BlueLiner)"}

USGS_SITE_URL = "https://waterservices.usgs.gov/nwis/site/"
# Census gazetteer: one directory per vintage
# (.../gazetteer/<year>_Gazetteer/<year>_Gaz_counties_national.zip -- note
# the SINGULAR "Gazetteer"). Census publishes yearly; try newest first so
# rebuilds pick up fresh vintages without a code change.
GAZ_VINTAGES = (2025, 2024, 2023, 2022, 2021, 2020)
GAZ_URL_TPL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
               "{year}_Gazetteer/{year}_Gaz_{kind}_national.zip")

COORD_PRECISION = 4  # ~11 m; plenty for a flyTo target


def parse_usgs_rdb(text: str, state: str) -> list[list]:
    """USGS RDB (tab-separated, '#' comments, a header row then a
    column-format row) -> [[site_no, name, state, lat, lon], ...]."""
    rows: list[list] = []
    header: list[str] | None = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if header is None:
            header = parts
            continue
        if parts and parts[0].endswith("s"):  # the "5s 15s..." format row
            if all(c.isdigit() or c == "s" for c in parts[0]):
                continue
        row = dict(zip(header, parts))
        site_no = (row.get("site_no") or "").strip()
        name = (row.get("station_nm") or "").strip()
        try:
            lat = round(float(row.get("dec_lat_va", "")), COORD_PRECISION)
            lon = round(float(row.get("dec_long_va", "")), COORD_PRECISION)
        except (TypeError, ValueError):
            continue
        if not site_no or not name:
            continue
        rows.append([site_no, name.title(), state, lat, lon])
    return rows


def fetch_gauges(codes: list[str]) -> list[list]:
    """Every active stream gauge per state, via the USGS site service."""
    import httpx

    out: list[list] = []
    with httpx.Client(timeout=60.0, headers=_UA) as c:
        for st in codes:
            r = c.get(USGS_SITE_URL, params={
                "format": "rdb", "stateCd": STATES[st]["usgs_code"],
                "siteType": "ST", "siteStatus": "active",
            })
            r.raise_for_status()
            rows = parse_usgs_rdb(r.text, st)
            out += rows
            print(f"[gauges] {st}: +{len(rows):,} ({len(out):,})", flush=True)
            time.sleep(0.3)
    return out


def parse_gazetteer(tsv: str, kind: str) -> list[list]:
    """Census gazetteer TSV -> [[name, state, lat, lon], ...]. Places
    carry suffixes like 'town'/'city'/'CDP' in NAME; strip the common
    ones so search reads naturally ('Parkton', not 'Parkton CDP')."""
    rows: list[list] = []
    header: list[str] | None = None
    for line in tsv.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split("\t")]
        if header is None:
            header = parts
            continue
        row = dict(zip(header, parts))
        st = row.get("USPS", "")
        name = row.get("NAME", "")
        if st not in STATES or not name:
            continue
        if kind == "place":
            for suffix in (" CDP", " city", " town", " village", " borough",
                           " municipality"):
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
        try:
            lat = round(float(row["INTPTLAT"]), COORD_PRECISION)
            lon = round(float(row["INTPTLONG"]), COORD_PRECISION)
        except (KeyError, TypeError, ValueError):
            continue
        rows.append([name, st, lat, lon])
    return rows


def fetch_gazetteer(kind: str) -> list[list]:
    """`kind` is the Census filename token: "counties" | "place". Tries
    the newest vintage first and falls back year by year (404s happen
    while a new vintage is mid-publish)."""
    import httpx

    with httpx.Client(timeout=120.0, headers=_UA,
                      follow_redirects=True) as c:
        r = None
        for year in GAZ_VINTAGES:
            url = GAZ_URL_TPL.format(year=year, kind=kind)
            resp = c.get(url)
            if resp.status_code == 200:
                r = resp
                print(f"[{kind}] vintage {year}", flush=True)
                break
            print(f"[{kind}] {year}: HTTP {resp.status_code}, "
                  f"trying older vintage", flush=True)
        if r is None:
            raise RuntimeError(f"no gazetteer vintage found for {kind}")
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    name = next(n for n in zf.namelist() if n.endswith(".txt"))
    # Gazetteer files are latin-1-ish; utf-8 with replacement is safe.
    tsv = zf.read(name).decode("utf-8", errors="replace")
    rows = parse_gazetteer(tsv, "county" if kind == "counties" else "place")
    print(f"[{kind}] {len(rows):,} rows", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", default=os.path.join(
        ROOT, "data", "search", "search_index.json.gz"))
    ap.add_argument("--states", default=None,
                    help="comma list (default: all lower-48)")
    ap.add_argument("--skip-census", action="store_true",
                    help="gauges only (local smoke test; census.gov needs"
                         " the CI runner's egress)")
    args = ap.parse_args()

    codes = ([s.strip().upper() for s in args.states.split(",") if s.strip()]
             if args.states else sorted(STATES))
    index = {
        "v": 1,
        "gauges": fetch_gauges(codes),
        "counties": [] if args.skip_census else fetch_gazetteer("counties"),
        "places": [] if args.skip_census else fetch_gazetteer("place"),
    }
    payload = json.dumps(index, separators=(",", ":"))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as f:
        f.write(payload)
    plain = args.out[:-3] if args.out.endswith(".gz") else args.out + ".json"
    with open(plain, "w", encoding="utf-8") as f:
        f.write(payload)
    print(f"[done] {len(index['gauges']):,} gauges, "
          f"{len(index['counties']):,} counties, "
          f"{len(index['places']):,} places -> {args.out} "
          f"({os.path.getsize(args.out):,} B gz, {len(payload):,} B raw)",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
