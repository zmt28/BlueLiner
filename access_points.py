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
import logging
import os

from shapely.geometry import shape

from arcgis import fetch_geojson_features
from cache import LruTtl

logger = logging.getLogger("blueliner.access")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "access_points")


def _baseline_states() -> list[str]:
    """States with a bundled data/access_points/<STATE>.json file."""
    if not os.path.isdir(_DATA_DIR):
        return []
    return sorted(
        fn[:-5] for fn in os.listdir(_DATA_DIR)
        if fn.endswith(".json") and len(fn) == 7 and fn[:2].isupper()
    )


def _load_baseline(state: str) -> list[dict]:
    """Read data/access_points/<STATE>.json. Returns [] when absent."""
    path = os.path.join(_DATA_DIR, f"{state}.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return json.load(f)


ACCESS_BASELINE: dict[str, list[dict]] = {
    state: _load_baseline(state) for state in _baseline_states()
}


def _load_sources() -> dict[str, list[dict]]:
    """Read the declarative live-feed registry
    (`data/access_points/sources.json`). Each source dict:
    {state, label, url, agency_url, name_field?, type_field?, fixed_type?,
     notes_field?, access?}. Grouped by state; a state may declare several
    layers (e.g. boat ramps + fishing piers). Every URL in the registry
    was verified against the live endpoint when added; the loader still
    degrades to the bundled baseline when one is unreachable."""
    path = os.path.join(_DATA_DIR, "sources.json")
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    by_state: dict[str, list[dict]] = {}
    for src in raw.get("sources", []):
        st = src.get("state")
        if st and src.get("url"):
            by_state.setdefault(st, []).append(src)
    return by_state


_TRUTHY = (1, "1", True, "Yes", "YES", "yes", "Y", "y", "true", "True")


def _truthy(v) -> bool:
    """Flag-column truthiness: Y/Yes/1/true strings, plus positive
    numbers (agencies like WDFW publish counts, e.g. BoatRamps=2)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return v in _TRUTHY


ACCESS_SOURCES: dict[str, list[dict]] = _load_sources()

# Field-name candidates (different agencies use different cases). We
# pick the first one that's populated, matching `stocking.py:67-70`.
_NAME_FIELDS = ("NAME", "Name", "FACILITY_N", "Facility_N", "SITE_NAME",
                "SiteName", "RAMP_NAME", "Water", "WATER")
_TYPE_FIELDS = ("TYPE", "Type", "ACCESS_TYP", "AccessType", "FACILITY_T")
_NOTES_FIELDS = ("DESCRIPTION", "Description", "NOTES", "Notes",
                 "RAMP_NOTES", "REMARKS")

# Sized for the whole state catalog now that any state can carry live
# feeds; TTL lets a transient fetch failure (cached baseline-only) heal
# without a restart.
_access_cache: LruTtl = LruTtl(maxsize=64, ttl=6 * 3600)


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


def _features_to_points(features: list[dict], src: dict) -> list[dict]:
    """Convert an ArcGIS GeoJSON FeatureCollection's features into
    canonical access-point dicts. Unparseable / non-point geometry is
    coerced to its centroid (handles agencies that publish ramps as
    small polygons rather than points)."""
    agency_url = src.get("agency_url") or src.get("url")
    name_field = src.get("name_field")
    notes_field = src.get("notes_field")
    fixed_type = src.get("fixed_type")
    type_field = src.get("type_field")
    type_flags = src.get("type_flags")  # ordered {flag column -> type}

    def _type_of(props: dict) -> str:
        if fixed_type:
            return fixed_type
        if type_flags:
            # Agencies like PFBC/MD DNR publish amenities as Y/N columns
            # (RAMP, PIER, SHORE_FISH); first truthy flag wins.
            for field, t in type_flags.items():
                if _truthy(props.get(field)):
                    return t
            return "walk_in"
        raw = (str(props.get(type_field))
               if type_field and props.get(type_field)
               else _pick(props, _TYPE_FIELDS))
        return _normalize_type(raw)

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
            name = ((str(props.get(name_field)) if props.get(name_field)
                     else None) if name_field
                    else _pick(props, _NAME_FIELDS)) or "Access point"
            points.append({
                "name": name,
                "lat": float(c.y),
                "lon": float(c.x),
                "type": _type_of(props),
                "access": src.get("access", "public"),
                "agency_url": agency_url,
                "notes": (str(props.get(notes_field))
                          if notes_field and props.get(notes_field)
                          else _pick(props, _NOTES_FIELDS)),
            })
        except Exception:
            continue
    return points


def load_access_points(state: str) -> list[dict]:
    """Baseline points for the state, plus any live overlays that
    respond. Cached per state."""
    if state in _access_cache and _access_cache[state] is not None:
        return _access_cache[state]

    points = [dict(p, source="baseline")
              for p in ACCESS_BASELINE.get(state, [])]

    for src in ACCESS_SOURCES.get(state, []):
        features = fetch_geojson_features(src["url"])
        if features:
            for p in _features_to_points(features, src):
                points.append(dict(p, source="live"))
        else:
            logger.info("access live feed unreachable: %s",
                        src.get("label", src["url"]))

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
