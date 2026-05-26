"""
Angler access points -- boat ramps, walk-in trails, fishing piers,
designated parking, wading-access points.

Per-state baselines live in `data/access_points/<STATE>.json` (zero
network, loaded at import). Where a state agency publishes a live
ArcGIS feature service for boating / fishing access, we overlay it on
top of the bundled baseline the same way `stocking.py` does for VA.

Canonical point shape:
    {name, lat, lon, type, access, agency_url, notes?}

`type ∈ {boat_ramp, walk_in, pier, parking, wading_access}` -- drives
marker styling on the map. `access ∈ {public, permit, fee,
private_easement}` -- drives a chip on the popup. `notes` is freeform
and optional ("trailer parking", "no motors", "permit required").
"""

import json
import os

from shapely.geometry import shape

from arcgis import fetch_geojson_features
from cache import LruTtl

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "access_points")


def _load_baseline(state: str) -> list[dict]:
    """Read data/access_points/<STATE>.json. Returns [] when absent."""
    path = os.path.join(_DATA_DIR, f"{state}.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


# Same four-state seed as stocking. New states drop in via the
# "Adding a new state" path documented in CONTRIBUTING.md.
ACCESS_BASELINE: dict[str, list[dict]] = {
    state: _load_baseline(state) for state in ("MD", "VA", "WV", "PA")
}

# Live ArcGIS endpoints where verified. Exact URLs differ per agency;
# entries below are best-effort and the loader degrades to baseline
# when the endpoint is wrong / unreachable / returns nothing. This
# mirrors the `STOCKING_SOURCES` pattern in `stocking.py:57-65`.
ACCESS_SOURCES: dict[str, dict] = {
    # TODO verify -- VA DWR publishes Public Fishing Lakes + Boat
    # Ramps via ArcGIS; the exact MapServer layer needs confirmation.
    # Until verified the bundled baseline ships VA's well-known
    # walk-in waters.
    #
    # "VA": {
    #     "name": "VA DWR Boating Access",
    #     "url": ("https://services.dwr.virginia.gov/arcgis/rest/services/"
    #             "Public/BoatingAccess/MapServer/0/query?where=1%3D1"),
    # },
    #
    # TODO MD -- MD DNR has a Boating Public Landings service; same
    # verification need.
    #
    # TODO PA -- PASDA hosts PA Fish & Boat boat-access points; need to
    # identify the right layer id within the PAFishBoat service.
}

# Field-name candidates (different agencies use different cases). We
# pick the first one that's populated, matching `stocking.py:67-70`.
_NAME_FIELDS = ("NAME", "Name", "FACILITY_N", "Facility_N", "SITE_NAME",
                "SiteName", "RAMP_NAME", "Water", "WATER")
_TYPE_FIELDS = ("TYPE", "Type", "ACCESS_TYP", "AccessType", "FACILITY_T")
_NOTES_FIELDS = ("DESCRIPTION", "Description", "NOTES", "Notes",
                 "RAMP_NOTES", "REMARKS")

_access_cache: LruTtl = LruTtl(maxsize=8)


def _pick(props: dict, fields: tuple[str, ...]) -> str | None:
    for f in fields:
        v = props.get(f)
        if v not in (None, ""):
            return str(v)
    return None


def _normalize_type(raw: str | None) -> str:
    """Map agency-specific access-type strings onto the canonical enum.
    Unknown values land on 'walk_in' as the safest assumption (most
    state access points are walk-in, and the icon doesn't promise a
    ramp the user might not find)."""
    if not raw:
        return "walk_in"
    s = raw.lower()
    if "ramp" in s or "launch" in s:
        return "boat_ramp"
    if "pier" in s or "platform" in s:
        return "pier"
    if "park" in s and "lot" in s:
        return "parking"
    if "wade" in s or "wading" in s:
        return "wading_access"
    return "walk_in"


def _features_to_points(features: list[dict], agency_url: str) -> list[dict]:
    """Convert an ArcGIS GeoJSON FeatureCollection's features into
    canonical access-point dicts. Unparseable / non-point geometry is
    coerced to its centroid (handles agencies that publish ramps as
    small polygons rather than points)."""
    points: list[dict] = []
    for f in features:
        try:
            g = f.get("geometry")
            if not g:
                continue
            geom = shape(g)
            if geom.is_empty:
                continue
            c = geom.centroid
            props = f.get("properties") or {}
            points.append({
                "name": _pick(props, _NAME_FIELDS) or "Access point",
                "lat": float(c.y),
                "lon": float(c.x),
                "type": _normalize_type(_pick(props, _TYPE_FIELDS)),
                "access": "public",  # live agency data is by definition public
                "agency_url": agency_url,
                "notes": _pick(props, _NOTES_FIELDS),
            })
        except Exception:
            continue
    return points


def load_access_points(state: str) -> list[dict]:
    """Baseline points for the state, plus the live overlay when
    available + reachable. Cached per state."""
    if state in _access_cache and _access_cache[state] is not None:
        return _access_cache[state]

    points = [dict(p, source="baseline")
              for p in ACCESS_BASELINE.get(state, [])]

    source = ACCESS_SOURCES.get(state)
    if source:
        features = fetch_geojson_features(source["url"])
        if features:
            agency_url = source.get("agency_url", source["url"])
            for p in _features_to_points(features, agency_url):
                points.append(dict(p, source="live"))

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
