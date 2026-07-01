#!/usr/bin/env python3
"""
Dump a national POINT overlay (dams | stocking) to a gzipped GeoJSON
FeatureCollection, the source for the PMTiles build (scripts/build_poi_tiles.sh).

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


LAYERS = {"dams": _dams_points, "stocking": _stocking_points}
DEFAULT_OUT = {
    "dams": os.path.join(ROOT, "data", "dams", "dams.geojson.gz"),
    "stocking": os.path.join(ROOT, "data", "stocking", "stocking.geojson.gz"),
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
