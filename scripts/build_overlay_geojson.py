#!/usr/bin/env python3
"""
Dump a national POINT overlay (dams | stocking | flyshops) to a gzipped
GeoJSON FeatureCollection, the source for the PMTiles build
(scripts/build_poi_tiles.sh).

Runs in the DATA-BUILD environment (egress to the NID FeatureServer / state
agency ArcGIS feeds) -- NOT the runtime image, NOT the Claude Code sandbox
(whose allowlist blocks those hosts). It reuses the very same per-state loaders
the retired /api/{dams,stocking} endpoints used, so the tiled data is identical
to what the app served -- just baked once at build time instead of loaded into
the 512 MB app process per request.

Access has its own producer (build_river_poi.py -> access.geojson.gz); this
covers the other two point overlays so all three ride the same tile pipeline.

    python scripts/build_overlay_geojson.py dams
    python scripts/build_overlay_geojson.py stocking --out data/stocking/stocking.geojson.gz
"""

import argparse
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from states import STATES  # noqa: E402

COORD_PRECISION = 5  # ~1.1 m


def _dams_points() -> list[dict]:
    """Every NID dam nationwide (the per-state loader, run across all states).
    NID's STATE column is single-valued, so a dam appears in one state's query;
    a light nid_id dedupe guards against any keyset-paging overlap."""
    import dams
    seen: set = set()
    out: list[dict] = []
    for st in STATES:
        for p in dams.load_dams(st):
            key = p.get("nid_id") or (round(p["lat"], COORD_PRECISION),
                                      round(p["lon"], COORD_PRECISION))
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        print(f"[dams] {st}: {len(out):,} cumulative", flush=True)
    return out


def _stocking_points() -> list[dict]:
    """Every stocked-water point nationwide: curated baselines + live agency
    feeds, merged by the same loader the /api/stocking endpoint used."""
    import stocking
    states = set(stocking.STOCKING_BASELINE) | set(stocking.STOCKING_SOURCES)
    out: list[dict] = []
    for st in sorted(states):
        pts = stocking.stocked_points(st)
        out += pts
        print(f"[stocking] {st}: +{len(pts):,} ({len(out):,} total)", flush=True)
    return out


OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_CONUS = (24.5, -125.0, 49.5, -66.9)   # (s, w, n, e)
_UA = {"User-Agent": "blueliner-data-build (github.com/zmt28/BlueLiner)"}


def _flyshops_points(rows: int = 4, cols: int = 6) -> list[dict]:
    """Every OSM fishing/fly shop in CONUS (shop=fishing covers fly shops,
    bait & tackle -- OSM tagging doesn't reliably distinguish them). Tiled
    Overpass sweep, same pattern as build_river_poi.fetch_osm_access; shops
    are sparse (~thousands nationwide) so a 4x6 grid stays under load limits.
    Ways/relations resolve via `out center`."""
    import time

    import httpx

    s, w, n, e = _CONUS
    dlat, dlon = (n - s) / rows, (e - w) / cols
    elements: list[dict] = []
    with httpx.Client(timeout=300.0, headers=_UA) as c:
        for r in range(rows):
            for col in range(cols):
                bs, bw = s + r * dlat, w + col * dlon
                bn, be = bs + dlat, bw + dlon
                q = (f"[out:json][timeout:240];"
                     f'nwr["shop"="fishing"]({bs},{bw},{bn},{be});'
                     f"out center tags;")
                for attempt in range(4):
                    try:
                        resp = c.post(OVERPASS_URL, data={"data": q})
                        if resp.status_code == 200:
                            elements += resp.json().get("elements", [])
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(5 * (attempt + 1))
                time.sleep(1)   # be polite to the public instance
            print(f"[flyshops] row {r + 1}/{rows}: {len(elements):,} raw",
                  flush=True)

    return _flyshop_elements_to_points(elements)


def _flyshop_elements_to_points(elements: list[dict]) -> list[dict]:
    """Overpass elements (node | way/relation with `center`) -> canonical
    point dicts. Split out from the fetch for testability."""
    seen: set = set()
    out: list[dict] = []
    for el in elements:
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        t = el.get("tags") or {}
        addr_bits = [x for x in (t.get("addr:housenumber"),
                                 t.get("addr:street")) if x]
        addr = " ".join(addr_bits)
        if t.get("addr:city"):
            addr = f"{addr}, {t['addr:city']}" if addr else t["addr:city"]
        out.append({
            "lat": lat,
            "lon": lon,
            "name": t.get("name") or "Fishing shop",
            "website": t.get("website") or t.get("contact:website") or "",
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "addr": addr,
            "source": "osm",
            "osm_id": f"{el.get('type')}/{el.get('id')}",
        })
    return out


LAYERS = {
    "dams": _dams_points,
    "stocking": _stocking_points,
    "flyshops": _flyshops_points,
}
DEFAULT_OUT = {
    "dams": os.path.join(ROOT, "data", "dams", "dams.geojson.gz"),
    "stocking": os.path.join(ROOT, "data", "stocking", "stocking.geojson.gz"),
    "flyshops": os.path.join(ROOT, "data", "fly_shops", "fly_shops.geojson.gz"),
}


def to_feature_collection(points: list[dict]) -> dict:
    """Canonical point dicts ({lat, lon, ...props}) -> GeoJSON. lat/lon fold
    into geometry; every other key travels as a tile property for popups."""
    features = []
    for p in points:
        try:
            lon = round(float(p["lon"]), COORD_PRECISION)
            lat = round(float(p["lat"]), COORD_PRECISION)
        except (KeyError, TypeError, ValueError):
            continue
        props = {k: v for k, v in p.items() if k not in ("lat", "lon")}
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props,
        })
    return {"type": "FeatureCollection", "features": features}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("layer", choices=sorted(LAYERS))
    ap.add_argument("--out", default=None, help="output .geojson.gz path")
    args = ap.parse_args()

    points = LAYERS[args.layer]()
    fc = to_feature_collection(points)
    out = args.out or DEFAULT_OUT[args.layer]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"[done] {len(fc['features']):,} {args.layer} features -> {out}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
