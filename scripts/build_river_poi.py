#!/usr/bin/env python3
"""
Build the national river-anchored ANGLER ACCESS overlay -- Phase 1 of
docs/river-poi-coverage-plan.md.

Replaces the hand-curated, approximate data/access_points/<ST>.json baselines
(which reverse-geocode to houses) with points carrying REAL, sourced
coordinates:

  - OpenStreetMap via Overpass: leisure=slipway (boat ramps), leisure=fishing
    (fishing/wading access). ODbL -- attribution "(c) OpenStreetMap
    contributors" required.
  - RIDB / Recreation.gov facilities (boat launches, fishing sites). Public
    domain. Needs RIDB_API_KEY (free); skipped if absent.
  - State agency ArcGIS feeds already declared in
    data/access_points/sources.json. Authoritative surveyed coordinates.

Every point is kept ONLY if it lies within --buffer-m of a reach in the
clickable stream network (data/nhdplus/clickable_streams.geojson.gz), measured
in EPSG:5070 metres -- so a ramp/lot far from water is dropped. Each kept point
is associated to that reach's levelpathid (panel placement) and normalized to:

    {lat, lon, name, type, access, source, source_id, precision, levelpathid}

`type` in {boat_ramp, walk_in, wading_access, pier, parking}; `precision` in
{surveyed, mapped} (agency/RIDB = surveyed, OSM = mapped). Cross-source dedupe
keeps the most authoritative coordinate (agency > RIDB > OSM).

Runs in the DATA-BUILD environment (CI / a dev box with geopandas-stack +
egress) -- NOT the runtime image, NOT the Claude Code sandbox (its egress
allowlist blocks Overpass / RIDB / state hosts). Output: a gzipped GeoJSON +
manifest, then PMTiles + R2 publish (a follow-up, mirroring build_trails.py).

The spatial-join and dedupe core uses shapely (an STRtree over the projected
stream network) so it's unit-testable without geopandas; only the source
fetchers and the lon/lat -> EPSG:5070 projection need the heavy deps + egress.
"""

import argparse
import gzip
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "access_points")
DEFAULT_OUT = os.path.join(OUT_DIR, "access.geojson.gz")
DEFAULT_MANIFEST = os.path.join(OUT_DIR, "access.manifest.json")
DEFAULT_STREAMS = os.path.join(
    ROOT, "data", "nhdplus", "clickable_streams.geojson.gz")

DEFAULT_BUFFER_M = 75.0     # ~half a typical riverside-lot depth; ramps hug water
COORD_PRECISION = 5         # ~1.1 m
DEDUPE_M = 40.0             # two points of the same type within this collapse

UA = {"User-Agent": "Blueliner-databuild/1.0 (+https://blueliner.app)"}

# Source authority order for dedupe (lower index wins the coordinate).
SOURCE_PRECEDENCE = ("agency", "ridb", "osm")
_PRECISION = {"agency": "surveyed", "ridb": "surveyed", "osm": "mapped"}


# --------------------------------------------------------------------------
# Normalization -- pure dict transforms (unit-testable, no deps)
# --------------------------------------------------------------------------

def _clean(v) -> str | None:
    if v in (None, "", "None"):
        return None
    s = str(v).strip()
    return s or None


def _osm_type(tags: dict) -> str:
    """OSM tag bundle -> our access `type`."""
    if tags.get("leisure") == "slipway":
        return "boat_ramp"
    # leisure=fishing with a pier/platform structure -> pier, else wading.
    if tags.get("man_made") == "pier" or tags.get("pier"):
        return "pier"
    return "wading_access"


def normalize_osm(elements: list[dict]) -> list[dict]:
    """Overpass elements (node | way/relation with a `center`) -> points."""
    out = []
    for el in elements:
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:                                   # way / relation
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if not _finite(lat) or not _finite(lon):
            continue
        tags = el.get("tags") or {}
        out.append({
            "lat": float(lat), "lon": float(lon),
            "name": _clean(tags.get("name")),
            "type": _osm_type(tags),
            "access": "public" if tags.get("access") not in
                      ("private", "no") else "private",
            "source": "osm",
            "source_id": f"{el.get('type','')[0]}{el.get('id')}",
        })
    return out


def normalize_ridb(facilities: list[dict]) -> list[dict]:
    """RIDB facility records -> points (boat launches / fishing sites)."""
    out = []
    for f in facilities:
        lat, lon = f.get("FacilityLatitude"), f.get("FacilityLongitude")
        if not _finite(lat) or not _finite(lon) or (lat == 0 and lon == 0):
            continue
        name = _clean(f.get("FacilityName"))
        t = (name or "").lower()
        typ = "boat_ramp" if ("launch" in t or "ramp" in t or "boat" in t) \
            else "walk_in"
        out.append({
            "lat": float(lat), "lon": float(lon), "name": name,
            "type": typ, "access": "public", "source": "ridb",
            "source_id": str(f.get("FacilityID")),
        })
    return out


def normalize_agency(features: list[dict], src: dict) -> list[dict]:
    """ArcGIS GeoJSON point features from an access source.json entry."""
    name_field = src.get("name_field")
    type_field = src.get("type_field")
    fixed_type = src.get("fixed_type")
    out = []
    for ft in features:
        geom = (ft.get("geometry") or {})
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or [None, None]
        lon, lat = coords[0], coords[1]
        if not _finite(lat) or not _finite(lon):
            continue
        props = ft.get("properties") or {}
        out.append({
            "lat": float(lat), "lon": float(lon),
            "name": _clean(props.get(name_field)) if name_field else None,
            "type": fixed_type or _agency_type(props.get(type_field)),
            "access": "public",
            "source": "agency",
            "source_id": f"{src.get('state','')}:{props.get('OBJECTID')}",
        })
    return out


_AGENCY_TYPE = {
    "ramp": "boat_ramp", "boat": "boat_ramp", "launch": "boat_ramp",
    "pier": "pier", "walk": "walk_in", "wade": "wading_access",
}


def _agency_type(v) -> str:
    s = (_clean(v) or "").lower()
    for k, t in _AGENCY_TYPE.items():
        if k in s:
            return t
    return "walk_in"


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Dedupe -- pure (unit-testable). Collapse same-type points within DEDUPE_M,
# keeping the most authoritative source's coordinate.
# --------------------------------------------------------------------------

def _haversine_m(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371000.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def dedupe(points: list[dict], radius_m: float = DEDUPE_M) -> list[dict]:
    """Greedy spatial dedupe within `type`: process by source precedence, drop
    a later point if an already-kept point of the same type is within radius."""
    order = sorted(points, key=lambda p: SOURCE_PRECEDENCE.index(
        p.get("source", "osm")) if p.get("source") in SOURCE_PRECEDENCE else 9)
    kept: list[dict] = []
    for p in order:
        dup = any(p["type"] == k["type"] and
                  _haversine_m(p["lat"], p["lon"], k["lat"], k["lon"]) <= radius_m
                  for k in kept)
        if not dup:
            kept.append(p)
    return kept


# --------------------------------------------------------------------------
# Clip + associate -- shapely STRtree over the (projected) stream network.
# `stream_geoms` are projected LineStrings (EPSG:5070 metres) carrying a
# `levelpathid`; `points_xy` are (x, y, record) in the same CRS. Testable with
# synthetic planar coordinates (no geopandas / pyproj needed).
# --------------------------------------------------------------------------

def clip_and_associate(points_xy, stream_geoms, levelpathids, buffer_m):
    """Keep points within buffer_m of a reach; stamp the nearest reach's
    levelpathid onto the record. Returns the kept records."""
    from shapely.geometry import Point
    from shapely import STRtree
    tree = STRtree(stream_geoms)
    out = []
    for x, y, rec in points_xy:
        pt = Point(x, y)
        idx = tree.query_nearest(pt, max_distance=buffer_m, return_distance=False,
                                 all_matches=False)
        if len(idx) == 0:
            continue
        i = int(idx[0])
        rec = dict(rec, levelpathid=levelpathids[i])
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# Source fetchers -- need egress; only run in the data-build environment.
# --------------------------------------------------------------------------

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# CONUS bbox tiles keep each Overpass query under its load limit.
_CONUS = (24.5, -125.0, 49.5, -66.9)   # (s, w, n, e)


def fetch_osm_access(rows=6, cols=8):
    """Overpass: leisure=slipway + leisure=fishing across CONUS, tiled."""
    import httpx
    import time
    s, w, n, e = _CONUS
    dlat, dlon = (n - s) / rows, (e - w) / cols
    elements = []
    with httpx.Client(timeout=300.0, headers=UA) as c:
        for r in range(rows):
            for col in range(cols):
                bs, bw = s + r * dlat, w + col * dlon
                bn, be = bs + dlat, bw + dlon
                q = (f"[out:json][timeout:240];("
                     f'nwr["leisure"="slipway"]({bs},{bw},{bn},{be});'
                     f'nwr["leisure"="fishing"]({bs},{bw},{bn},{be});'
                     f");out center tags;")
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
    return elements


def fetch_ridb_access():
    """RIDB facilities flagged for fishing/boating. Needs RIDB_API_KEY."""
    key = os.environ.get("RIDB_API_KEY")
    if not key:
        print("[ridb] RIDB_API_KEY not set -- skipping RIDB")
        return []
    import httpx
    out, offset = [], 0
    with httpx.Client(timeout=60.0, headers={**UA, "apikey": key}) as c:
        while True:
            r = c.get("https://ridb.recreation.gov/api/v1/facilities",
                      params={"activity": "FISHING,BOATING", "limit": 50,
                              "offset": offset})
            if r.status_code != 200:
                break
            recs = r.json().get("RECDATA", [])
            if not recs:
                break
            out += recs
            offset += len(recs)
    return out


def fetch_agency_access():
    """Reuse the verified access registry + the shared ArcGIS fetcher."""
    sys.path.insert(0, ROOT)
    from arcgis import fetch_geojson_features
    path = os.path.join(OUT_DIR, "sources.json")
    if not os.path.exists(path):
        return []
    raw = json.load(open(path)).get("sources", [])
    out = []
    for src in raw:
        if not src.get("url"):
            continue
        try:
            # fetch_geojson_features returns None (not []) on an empty or
            # flapping feed; the runtime loader degrades the same way. Coerce
            # so a transiently-down state feed contributes 0, not a crash.
            feats = fetch_geojson_features(src["url"]) or []
            out += normalize_agency(feats, src)
        except Exception as exc:                       # noqa: BLE001
            print(f"[agency] {src.get('state')} {src.get('label')}: {exc}")
    return out


# --------------------------------------------------------------------------

def load_stream_geoms(streams_path: str):
    """Load clickable-streams as projected (EPSG:5070) shapely LineStrings +
    their levelpathids. Uses pyproj for the projection (data-build only)."""
    from shapely.geometry import shape
    from shapely.ops import transform
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    geoms, lpids = [], []
    with gzip.open(streams_path, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    for ft in fc.get("features", []):
        g = ft.get("geometry")
        if not g:
            continue
        try:
            pg = transform(tf.transform, shape(g))
        except Exception:                              # noqa: BLE001
            continue
        geoms.append(pg)
        lpids.append((ft.get("properties") or {}).get("levelpathid"))
    return geoms, lpids


def project_points(points: list[dict]):
    """(record) -> (x, y, record) in EPSG:5070."""
    from pyproj import Transformer
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    out = []
    for p in points:
        x, y = tf.transform(p["lon"], p["lat"])
        out.append((x, y, p))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--streams", default=DEFAULT_STREAMS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    ap.add_argument("--sources", default="osm,ridb,agency",
                    help="comma list of sources to pull")
    ap.add_argument("--probe", action="store_true",
                    help="fetch + report per-source counts, do not clip/write")
    args = ap.parse_args()

    want = set(s.strip() for s in args.sources.split(",") if s.strip())
    raw: list[dict] = []
    if "agency" in want:
        a = fetch_agency_access(); print(f"[agency] {len(a):,} points"); raw += a
    if "ridb" in want:
        r = normalize_ridb(fetch_ridb_access())
        print(f"[ridb]   {len(r):,} points"); raw += r
    if "osm" in want:
        o = normalize_osm(fetch_osm_access())
        print(f"[osm]    {len(o):,} points"); raw += o
    print(f"[raw] {len(raw):,} points before clip/dedupe")
    if args.probe:
        return 0

    if not os.path.exists(args.streams):
        print(f"ERROR: stream network not found at {args.streams}",
              file=sys.stderr)
        return 1
    geoms, lpids = load_stream_geoms(args.streams)
    print(f"[streams] {len(geoms):,} reaches (EPSG:5070)")
    kept = clip_and_associate(project_points(raw), geoms, lpids, args.buffer_m)
    print(f"[clip] {len(kept):,} within {args.buffer_m:.0f} m of a reach")
    final = dedupe(kept)
    print(f"[dedupe] {len(final):,} after cross-source dedupe")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with gzip.open(args.out, "wt", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[')
        for i, p in enumerate(final):
            feat = {"type": "Feature",
                    "geometry": {"type": "Point",
                                 "coordinates": [round(p["lon"], COORD_PRECISION),
                                                 round(p["lat"], COORD_PRECISION)]},
                    "properties": {k: p.get(k) for k in
                                   ("name", "type", "access", "source",
                                    "source_id", "precision", "levelpathid")}}
            feat["properties"]["precision"] = _PRECISION.get(p.get("source"))
            out.write(("," if i else "") + json.dumps(feat, separators=(",", ":")))
        out.write("]}")
    by_source = {s: sum(1 for p in final if p.get("source") == s)
                 for s in SOURCE_PRECEDENCE}
    by_type: dict = {}
    for p in final:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    json.dump({"feature_count": len(final), "by_source": by_source,
               "by_type": by_type, "buffer_m": args.buffer_m,
               "attribution": "Includes data (c) OpenStreetMap contributors "
                              "(ODbL), RIDB/Recreation.gov (public domain), and "
                              "state agencies."},
              open(args.manifest, "w"), indent=2)
    print(f"[done] {len(final):,} points -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
