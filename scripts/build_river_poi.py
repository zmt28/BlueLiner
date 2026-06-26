#!/usr/bin/env python3
"""
Build the national river-anchored ANGLER ACCESS overlay -- Phase 1 of
docs/river-poi-coverage-plan.md.

Replaces the hand-curated, approximate data/access_points/<ST>.json baselines
(which reverse-geocode to houses) with points carrying REAL, sourced
coordinates:

  - OpenStreetMap, by default from the national Geofabrik extract (osmium
    streams the ~10 GB pbf in bounded memory; --osm-mode overpass keeps the
    legacy tiled sweep for a quick check): leisure=slipway (boat ramps),
    leisure=fishing (fishing/wading), man_made=pier (piers), amenity=parking
    (river-bank lots), highway=trailhead (walk-in put-ins). ODbL -- attribution
    "(c) OpenStreetMap contributors" required.
  - RIDB / Recreation.gov facilities (boat launches, fishing sites). Public
    domain. Needs RIDB_API_KEY (free); skipped if absent.
  - State agency ArcGIS feeds already declared in
    data/access_points/sources.json. Authoritative surveyed coordinates.

Every point is kept ONLY if it lies within --buffer-m of a reach in the
clickable stream network (data/nhdplus/clickable_streams.geojson.gz), measured
in EPSG:5070 metres -- so a ramp/lot far from water is dropped. Each kept point
is associated to that reach's levelpathid (panel placement) and normalized to:

    {lat, lon, name, type, access, source, source_id, precision, levelpathid}

`type` in {boat_ramp, walk_in, wading_access, pier, parking} (trailheads ride
the walk_in bucket -- the map has no trailhead glyph); `precision` in
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

DEFAULT_BUFFER_M = 150.0    # river-bank lots/trailheads sit a parking-lot's
                            # depth back from the water; 75 m clipped them out
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


def _osm_type(tags: dict) -> str | None:
    """OSM tag bundle -> our access `type`, or None when the feature carries
    none of the access tags we pull (so a stray element from a broad export is
    dropped rather than mis-bucketed)."""
    if tags.get("leisure") == "slipway":
        return "boat_ramp"
    # leisure=fishing with a pier/platform structure -> pier, else wading.
    if tags.get("man_made") == "pier" or tags.get("pier"):
        return "pier"
    if tags.get("amenity") == "parking":
        return "parking"
    # A trailhead is a walk-in put-in; the map has no trailhead glyph, so it
    # rides the walk_in bucket (which is also makePoiElement's fallback).
    if tags.get("highway") == "trailhead":
        return "walk_in"
    if tags.get("leisure") == "fishing":
        return "wading_access"
    return None


def _osm_point(tags: dict, lat, lon, source_id: str | None) -> dict | None:
    """Shared OSM -> canonical access point (used by both the Overpass and the
    Geofabrik paths). None when the tags aren't an access type we keep, or the
    coordinate is unusable."""
    if not _finite(lat) or not _finite(lon):
        return None
    typ = _osm_type(tags)
    if typ is None:
        return None
    return {
        "lat": float(lat), "lon": float(lon),
        "name": _clean(tags.get("name")),
        "type": typ,
        "access": "public" if tags.get("access") not in
                  ("private", "no") else "private",
        "source": "osm",
        "source_id": source_id,
    }


def normalize_osm(elements: list[dict]) -> list[dict]:
    """Overpass elements (node | way/relation with a `center`) -> points."""
    out = []
    for el in elements:
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:                                   # way / relation
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        sid = f"{el.get('type','')[:1]}{el.get('id')}"
        p = _osm_point(el.get("tags") or {}, lat, lon, sid)
        if p is not None:
            out.append(p)
    return out


def _fast_lonlat(geom: dict):
    """(lon, lat) for an `osmium export` geometry WITHOUT building a shapely
    object -- the parking sweep is millions of features, so shape().centroid
    per feature is the build's hot loop. Point -> its coord; line/area -> the
    mean of the first ring's vertices (a parking lot / slipway is small, so the
    ring mean is an apt marker anchor). None on malformed geometry."""
    coords = geom.get("coordinates")
    if not coords:
        return None
    if geom.get("type") == "Point":
        try:
            return float(coords[0]), float(coords[1])
        except (TypeError, ValueError, IndexError):
            return None
    ring = coords
    try:
        # descend nested rings (LineString/Polygon/MultiPolygon) to a flat list
        # of [lon, lat] pairs.
        while ring and isinstance(ring[0][0], (list, tuple)):
            ring = ring[0]
        n = len(ring)
        if not n:
            return None
        return sum(p[0] for p in ring) / n, sum(p[1] for p in ring) / n
    except (TypeError, ValueError, IndexError):
        return None


def normalize_osm_geojson(feat: dict) -> dict | None:
    """One GeoJSON feature from `osmium export` (Geofabrik path) -> point.
    Ways/areas export as Line/Polygon geometry; we anchor them at the ring
    mean so a parking lot or slipway way lands as a single marker."""
    geom = feat.get("geometry")
    if not geom:
        return None
    ll = _fast_lonlat(geom)
    if ll is None:
        return None
    lon, lat = ll
    props = feat.get("properties") or {}
    return _osm_point(props, lat, lon, _clean(feat.get("id")))


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


_TRUTHY = (1, "1", True, "Yes", "YES", "yes", "Y", "y", "true", "True")


def _truthy(v) -> bool:
    """Flag-column truthiness: Y/Yes/1/true strings, plus positive numbers
    (agencies like WDFW publish counts, e.g. BoatRamps=2)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return v in _TRUTHY


def _agency_type(props: dict, src: dict) -> str:
    """Resolve an agency feature's access type, honoring the source.json
    field mappings (carried over from the retired runtime loader so the
    overlay is no worse than the live feeds it replaces):
      fixed_type   -- every row is one type
      type_flags   -- ordered {Y/N column -> type}; first truthy wins
      type_field   -- a free-text type string, keyword-normalized
    """
    fixed_type = src.get("fixed_type")
    if fixed_type:
        return fixed_type
    type_flags = src.get("type_flags")
    if type_flags:
        for field, t in type_flags.items():
            if _truthy(props.get(field)):
                return t
        return "walk_in"
    return _normalize_type(_clean(props.get(src.get("type_field"))))


_TYPE_KEYWORDS = (
    ("ramp", "boat_ramp"), ("launch", "boat_ramp"), ("boat", "boat_ramp"),
    ("pier", "pier"), ("platform", "pier"),
    ("wade", "wading_access"), ("wading", "wading_access"),
)


def _normalize_type(raw: str | None) -> str:
    """Agency type string -> canonical enum. Unknown -> walk_in (the safest
    assumption; the glyph doesn't promise a ramp the angler can't find)."""
    if not raw:
        return "walk_in"
    s = raw.lower()
    if "park" in s and "lot" in s:
        return "parking"
    for kw, t in _TYPE_KEYWORDS:
        if kw in s:
            return t
    return "walk_in"


def normalize_agency(features: list[dict], src: dict) -> list[dict]:
    """ArcGIS GeoJSON features from an access source.json entry -> points.
    Honors fixed_type/type_flags/type_field, notes_field, access, and the
    per-source `dedupe` (one pin per named water per ~1 km cell -- collapses
    parcel-per-row easement layers like NY PFR). Non-point geometry is coerced
    to its centroid (agencies sometimes publish ramps as small polygons)."""
    from shapely.geometry import shape
    name_field = src.get("name_field")
    notes_field = src.get("notes_field")
    agency_url = src.get("agency_url") or src.get("url")
    do_dedupe = bool(src.get("dedupe"))
    out: list[dict] = []
    seen: set[tuple] = set()
    for ft in features:
        geom = ft.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom)
            if g.is_empty:
                continue
            c = g.centroid
            lat, lon = float(c.y), float(c.x)
        except Exception:                              # noqa: BLE001
            continue
        if not _finite(lat) or not _finite(lon):
            continue
        props = ft.get("properties") or {}
        name = (_clean(props.get(name_field)) if name_field else None) \
            or "Access point"
        if do_dedupe:
            key = (name.strip().lower(), round(lat, 2), round(lon, 2))
            if key in seen:
                continue
            seen.add(key)
        out.append({
            "lat": lat, "lon": lon,
            "name": name,
            "type": _agency_type(props, src),
            "access": src.get("access", "public"),
            "notes": _clean(props.get(notes_field)) if notes_field else None,
            "agency_url": agency_url,
            "source": "agency",
            "source_id": f"{src.get('state','')}:{props.get('OBJECTID')}",
        })
    return out


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

def _associate_idx(tree, x, y, buffer_m):
    """Nearest reach index within buffer_m of (x, y), or None."""
    from shapely.geometry import Point
    idx = tree.query_nearest(Point(x, y), max_distance=buffer_m,
                             return_distance=False, all_matches=False)
    return int(idx[0]) if len(idx) else None


def clip_and_associate(points_xy, stream_geoms, levelpathids, buffer_m):
    """Keep points within buffer_m of a reach; stamp the nearest reach's
    levelpathid onto the record. Returns the kept records.

    Per-point reference implementation (used by the unit tests); the build
    itself uses the chunked, vectorized `clip_records` over the millions of OSM
    parking features."""
    from shapely import STRtree
    tree = STRtree(stream_geoms)
    out = []
    for x, y, rec in points_xy:
        i = _associate_idx(tree, x, y, buffer_m)
        if i is None:
            continue
        out.append(dict(rec, levelpathid=levelpathids[i]))
    return out


def clip_records(records, tree, levelpathids, transformer, buffer_m,
                 chunk=250_000, label=""):
    """Project (EPSG:5070) + river-clip an iterable of {lon,lat,...} records,
    stamping the nearest reach's levelpathid. Vectorized in chunks: each chunk
    is one bulk pyproj projection + one `STRtree.query_nearest` over the whole
    point array (C-level), instead of a Python call per point -- the difference
    between minutes and hours once OSM `amenity=parking` is in the mix. Chunking
    keeps the multi-million parking set out of memory; only survivors are kept."""
    import time
    import numpy as np
    import shapely
    survivors: list[dict] = []
    lons: list[float] = []
    lats: list[float] = []
    recs: list[dict] = []
    seen = 0
    t0 = time.monotonic()

    def _flush():
        if not lons:
            return
        xs, ys = transformer.transform(lons, lats)
        pts = shapely.points(np.asarray(xs, dtype="float64"),
                             np.asarray(ys, dtype="float64"))
        res = tree.query_nearest(pts, max_distance=buffer_m,
                                 all_matches=False, return_distance=False)
        # res is (2, K): row 0 = input index, row 1 = reach index.
        for in_i, reach_i in zip(res[0].tolist(), res[1].tolist()):
            survivors.append(dict(recs[in_i], levelpathid=levelpathids[reach_i]))
        lons.clear(); lats.clear(); recs.clear()

    for r in records:
        lons.append(r["lon"]); lats.append(r["lat"]); recs.append(r)
        seen += 1
        if len(lons) >= chunk:
            _flush()
            print(f"[clip{' ' + label if label else ''}] {seen:,} seen, "
                  f"{len(survivors):,} kept, {time.monotonic() - t0:.0f}s",
                  flush=True)
    _flush()
    return survivors


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


# Geofabrik national extract -- the scalable OSM source. Overpass throttles a
# CONUS sweep to ~80 min and can't serve `amenity=parking` nationwide at all;
# osmium streams the ~10 GB pbf in bounded memory, so we filter to just the
# access tags, export to GeoJSON-Seq, and stream-clip against the river network.
GEOFABRIK_US = "https://download.geofabrik.de/north-america/us-latest.osm.pbf"

# osmium tags-filter expressions. n/w/r = node/way/relation; bare key=val keeps
# any object with that tag. Mirrors _osm_type's accepted tags.
OSM_TAG_FILTER = [
    "nwr/leisure=slipway", "nwr/leisure=fishing",
    "nwr/man_made=pier", "nwr/amenity=parking",
    "nwr/highway=trailhead",
]


def _download(url: str, dest: str) -> None:
    """Stream a (possibly multi-GB) file to `dest`, atomic on success. Logs
    throughput periodically so a slow/throttled mirror is visible in CI."""
    import httpx
    import time
    tmp = dest + ".part"
    print(f"[osm] downloading {url} -> {dest}", flush=True)
    t0 = time.monotonic()
    got = 0
    next_mark = 1 << 30                                 # log every ~1 GB
    with httpx.stream("GET", url, timeout=None, follow_redirects=True,
                      headers=UA) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=4 << 20):
                f.write(chunk)
                got += len(chunk)
                if got >= next_mark:
                    el = time.monotonic() - t0
                    print(f"[osm]   {got/1e9:.1f} GB in {el:.0f}s "
                          f"({got/1e6/max(el,1):.1f} MB/s)", flush=True)
                    next_mark += 1 << 30
    os.replace(tmp, dest)
    el = time.monotonic() - t0
    print(f"[osm] downloaded {os.path.getsize(dest)/1e9:.1f} GB in {el:.0f}s",
          flush=True)


def osm_geojsonseq_path(workdir: str, pbf_path: str | None = None) -> str:
    """Download (if needed) the Geofabrik US extract, filter it to the access
    tags with osmium, and export to a GeoJSON-Seq file. Returns its path.
    Requires the `osmium` CLI (osmium-tool); raises if it's missing."""
    import shutil
    import subprocess
    import time
    if shutil.which("osmium") is None:
        raise RuntimeError(
            "osmium CLI not found -- install osmium-tool (apt-get install "
            "osmium-tool) in the data-build environment")
    os.makedirs(workdir, exist_ok=True)
    pbf = pbf_path or os.path.join(workdir, "us-latest.osm.pbf")
    if not os.path.exists(pbf):
        _download(GEOFABRIK_US, pbf)
    filtered = os.path.join(workdir, "access-filtered.osm.pbf")
    print(f"[osm] osmium tags-filter -> {filtered}", flush=True)
    t0 = time.monotonic()
    subprocess.run(["osmium", "tags-filter", "--overwrite", "-o", filtered,
                    pbf, *OSM_TAG_FILTER], check=True)
    print(f"[osm] tags-filter done in {time.monotonic()-t0:.0f}s "
          f"({os.path.getsize(filtered)/1e6:.0f} MB)", flush=True)
    seq = os.path.join(workdir, "access-filtered.geojsonseq")
    print(f"[osm] osmium export -> {seq}", flush=True)
    t0 = time.monotonic()
    # -u type_id gives each feature a stable "n123"/"w456" id (our source_id).
    subprocess.run(["osmium", "export", "--overwrite", "-f", "geojsonseq",
                    "-u", "type_id", "-o", seq, filtered], check=True)
    print(f"[osm] export done in {time.monotonic()-t0:.0f}s "
          f"({os.path.getsize(seq)/1e9:.1f} GB)", flush=True)
    return seq


def stream_osm_geojsonseq(seq_path: str):
    """Yield normalized OSM access points from an `osmium export` GeoJSON-Seq
    file, one feature at a time (RFC 8142: each record may be prefixed with a
    RS control char). Streaming keeps the multi-million-feature parking set out
    of memory -- the caller clips each point as it arrives."""
    with open(seq_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip().lstrip("\x1e")
            if not line:
                continue
            try:
                feat = json.loads(line)
            except ValueError:
                continue
            p = normalize_osm_geojson(feat)
            if p is not None:
                yield p


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


def _probe_geofabrik_osm() -> None:
    """Reachability check for the Geofabrik path WITHOUT the ~10 GB download:
    osmium present? Geofabrik extract reachable?"""
    import shutil
    import httpx
    has_osmium = shutil.which("osmium") is not None
    print(f"[osm] osmium CLI: {'present' if has_osmium else 'MISSING'}")
    try:
        r = httpx.head(GEOFABRIK_US, follow_redirects=True, timeout=30.0,
                       headers=UA)
        size = int(r.headers.get("content-length", 0)) / 1e9
        print(f"[osm] {GEOFABRIK_US} -> HTTP {r.status_code} (~{size:.1f} GB)")
    except Exception as exc:                               # noqa: BLE001
        print(f"[osm] Geofabrik HEAD failed: {exc}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--streams", default=DEFAULT_STREAMS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    ap.add_argument("--sources", default="osm,ridb,agency",
                    help="comma list of sources to pull")
    ap.add_argument("--osm-mode", choices=("geofabrik", "overpass"),
                    default="geofabrik",
                    help="OSM source: national Geofabrik extract (scales to "
                         "parking/trailheads) or the legacy Overpass sweep")
    ap.add_argument("--osm-workdir", default=os.path.join(OUT_DIR, "_osm"),
                    help="scratch dir for the Geofabrik pbf + osmium output")
    ap.add_argument("--osm-pbf", default=None,
                    help="pre-downloaded Geofabrik .osm.pbf (skips the fetch)")
    ap.add_argument("--probe", action="store_true",
                    help="fetch + report per-source counts, do not clip/write")
    args = ap.parse_args()

    want = set(s.strip() for s in args.sources.split(",") if s.strip())

    # Small, authoritative sources are pulled into memory and batch-clipped.
    raw: list[dict] = []
    if "agency" in want:
        a = fetch_agency_access()
        print(f"[agency] {len(a):,} points", flush=True); raw += a
    if "ridb" in want:
        r = normalize_ridb(fetch_ridb_access())
        print(f"[ridb]   {len(r):,} points", flush=True); raw += r

    if args.probe:
        if "osm" in want:
            if args.osm_mode == "overpass":
                o = normalize_osm(fetch_osm_access())
                print(f"[osm]    {len(o):,} points (overpass)", flush=True)
            else:
                _probe_geofabrik_osm()
        print(f"[raw] {len(raw):,} non-OSM points before clip/dedupe",
              flush=True)
        return 0

    if not os.path.exists(args.streams):
        print(f"ERROR: stream network not found at {args.streams}",
              file=sys.stderr)
        return 1
    import time as _time
    from shapely import STRtree
    from pyproj import Transformer
    t0 = _time.monotonic()
    geoms, lpids = load_stream_geoms(args.streams)
    tree = STRtree(geoms)
    tf = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True)
    print(f"[streams] {len(geoms):,} reaches (EPSG:5070) + STRtree in "
          f"{_time.monotonic() - t0:.0f}s", flush=True)

    kept = clip_records(raw, tree, lpids, tf, args.buffer_m, label="non-osm")
    print(f"[clip] {len(kept):,} non-OSM within {args.buffer_m:.0f} m",
          flush=True)
    if "osm" in want:
        if args.osm_mode == "overpass":
            osm_pts = normalize_osm(fetch_osm_access())
            print(f"[osm]    {len(osm_pts):,} points (overpass)", flush=True)
            osm_kept = clip_records(osm_pts, tree, lpids, tf, args.buffer_m,
                                    label="osm")
        else:
            seq = osm_geojsonseq_path(args.osm_workdir, args.osm_pbf)
            osm_kept = clip_records(stream_osm_geojsonseq(seq), tree, lpids, tf,
                                    args.buffer_m, label="osm")
        print(f"[clip] {len(osm_kept):,} OSM within {args.buffer_m:.0f} m",
              flush=True)
        kept += osm_kept
    print(f"[clip] {len(kept):,} total within {args.buffer_m:.0f} m of a reach",
          flush=True)
    final = dedupe(kept)
    print(f"[dedupe] {len(final):,} after cross-source dedupe", flush=True)

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
                                    "source_id", "precision", "levelpathid",
                                    "notes", "agency_url")}}
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
