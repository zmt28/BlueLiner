"""
Angler access points -- boat ramps, walk-in trails, fishing piers,
designated parking, wading-access points.

Served entirely from the pre-built national, river-clipped overlay
(`scripts/build_river_poi.py` -> R2: `access.geojson.gz`). That overlay
already incorporates OSM, RIDB, and the state-agency ArcGIS feeds with
accurate, source-tagged coordinates, so the old hand-curated per-state
baselines + the request-time live-feed fetch have been retired (they
reverse-geocoded to houses and went stale behind the CDN). The live-feed
*registry* (`data/access_points/sources.json`) lives on as a build input.

Canonical point shape (overlay feature properties):
    {name, type, access, source, source_id, precision, levelpathid}

`type ∈ {boat_ramp, walk_in, pier, parking, wading_access}` -- drives
marker styling on the map. `access ∈ {public, private}` -- drives a chip on
the popup. `source ∈ {osm, ridb, agency}` and `precision ∈ {surveyed,
mapped}` tell the user how the coordinate was sourced.
"""

import json
import logging
import gzip
import os

import data_source
from cache import LruTtl
from states import point_in_state

logger = logging.getLogger("blueliner.access")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "access_points")
# Pre-built national, river-clipped access overlay (scripts/build_river_poi.py
# -> R2). The sole source of access points now that the baselines are retired.
_OVERLAY_BUNDLED = os.path.join(_DATA_DIR, "access.geojson.gz")

# Per-state cache of the overlay slice. TTL is a safety net; the overlay is
# immutable for a process, so this is effectively load-once.
_access_cache: LruTtl = LruTtl(maxsize=64, ttl=6 * 3600)

_overlay_by_state: dict[str, list[dict]] | None = None
_overlay_loaded = False


def _national_overlay() -> dict[str, list[dict]] | None:
    """The pre-built national river-clipped access overlay, grouped by state
    (each point state-tagged via point_in_state). None when unavailable -- e.g.
    a dev box without DATA_BASE_URL -- so the caller falls back to the curated
    baselines. Loaded + indexed once."""
    global _overlay_by_state, _overlay_loaded
    if _overlay_loaded:
        return _overlay_by_state
    _overlay_loaded = True
    path = data_source.resolve_data_file(_OVERLAY_BUNDLED, "access.geojson.gz")
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            fc = json.load(f)
    except Exception as exc:                           # noqa: BLE001
        logger.warning("access overlay load failed: %s", exc)
        return None
    by_state: dict[str, list[dict]] = {}
    for ft in fc.get("features", []):
        geom = ft.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = (geom.get("coordinates") or [None, None])
        lon, lat = coords[0], coords[1]
        if lat is None or lon is None:
            continue
        st = point_in_state(lat, lon)
        if not st:
            continue
        p = dict(ft.get("properties") or {})
        p["lat"], p["lon"] = lat, lon
        by_state.setdefault(st, []).append(p)
    logger.info("access overlay: %d points across %d states",
                sum(len(v) for v in by_state.values()), len(by_state))
    _overlay_by_state = by_state
    return by_state


def load_access_points(state: str) -> list[dict]:
    """Access points for a state, from the pre-built national overlay
    (accurate, river-clipped, sourced). Returns [] when the overlay is
    unavailable (e.g. a dev box without DATA_BASE_URL and no bundled file).
    Cached per state."""
    if state in _access_cache and _access_cache[state] is not None:
        return _access_cache[state]
    overlay = _national_overlay()
    points = overlay.get(state, []) if overlay is not None else []
    _access_cache[state] = points
    return points


def access_points_geojson(state: str) -> dict:
    """GeoJSON FeatureCollection for `/api/access?state=`. Geometry is
    a Point per access; the full canonical dict travels as properties
    so the client can render type-coded icons + popup chips without
    extra requests."""
    features: list[dict] = []
    for p in load_access_points(state):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [p["lon"], p["lat"]]},
            "properties": {k: v for k, v in p.items()
                           if k not in ("lat", "lon")},
        })
    return {"type": "FeatureCollection", "features": features}


def nearby_access(lat: float, lon: float, points: list[dict],
                  buffer_deg: float = 0.02) -> list[dict]:
    """Access points within ~buffer_deg (~2 km), nearest first.
    Mirrors `stocking.nearby_stocked` so the river-popup path can use
    either uniformly."""
    b2 = buffer_deg * buffer_deg
    hits: list[tuple[float, dict]] = []
    for p in points:
        d2 = (lat - p["lat"]) ** 2 + (lon - p["lon"]) ** 2
        if d2 <= b2:
            hits.append((d2, p))
    hits.sort(key=lambda h: h[0])
    return [p for _, p in hits]


def is_near_access(lat: float, lon: float, points: list[dict],
                   buffer_deg: float = 0.02) -> bool:
    return bool(nearby_access(lat, lon, points, buffer_deg))
