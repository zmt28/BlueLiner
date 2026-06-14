"""
Dams on rivers -- USACE National Inventory of Dams (NID).

One national source: the **NID_v1** hosted FeatureServer (federal public
domain, ~92k dams; verified live via the gis-endpoint-verify workflow on
2026-06-14). Queried per state (the app is state-scoped) and cached,
mirroring `stocking.py` / `access_points.py`. There is no bundled baseline
-- NID is the authoritative national layer, so there's nothing to seed and
nothing to fall back to; if the service is unreachable the layer just
doesn't show.

Canonical point shape:
    {name, lat, lon, river, owner, city, purposes?, height_ft?, year?,
     nid_id, agency_url}
"""

import logging

from shapely.geometry import shape

from arcgis import fetch_geojson_features
from cache import LruTtl
from states import STATES

logger = logging.getLogger("blueliner.dams")

# NID_v1 hosted FeatureServer, layer 0 -- the Esri_US_Federal_Data
# authoritative mirror of the USACE National Inventory of Dams. Verified
# live (gis-endpoint-verify, 2026-06-14): 92,469 points, public domain.
NID_QUERY_URL = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/"
                 "services/NID_v1/FeatureServer/0/query")
NID_AGENCY_URL = "https://nid.sec.usace.army.mil/"

# Dams are static reference data (NID refreshes ~annually), so a long TTL is
# fine; the per-state result is small (tens-to-low-thousands of points).
_dams_cache: LruTtl = LruTtl(maxsize=64, ttl=24 * 3600)

# NID_v1 uses the modern NAME / RIVER_OR_STREAM columns; height / purpose /
# year live past the confirmed field-list cap, so pick them defensively
# across the modern AND legacy NID column names (the AGOL republishes vary).
_NAME_FIELDS = ("NAME", "DAM_NAME", "OTHER_NAMES")
_RIVER_FIELDS = ("RIVER_OR_STREAM", "RIVER")
_OWNER_FIELDS = ("PRIMARY_OWNER_TYPE", "OWNER_TYPES", "OWNER_TYPE", "OWNER_NAME")
_CITY_FIELDS = ("CITY",)
_PURPOSE_FIELDS = ("PRIMARY_PURPOSE", "PURPOSES", "PURPOSE")
_HEIGHT_FIELDS = ("NID_HEIGHT", "DAM_HEIGHT", "STRUCTURAL_HEIGHT", "MAX_HEIGHT")
_YEAR_FIELDS = ("YEAR_COMPLETED", "YEAR_BUILT", "COMPLETED")
_NID_FIELDS = ("NIDID", "NID_ID", "FEDERAL_ID")

# NID encodes "no value" as the string "None" (seen live), so treat it the
# same as an empty cell.
_EMPTY = (None, "", "None")


def _pick(props: dict, fields: tuple[str, ...]) -> str | None:
    # NID string columns are fixed-width and arrive space-padded
    # ("Rockbound Creek               "), so strip and re-test for empty.
    for f in fields:
        v = props.get(f)
        if v in _EMPTY:
            continue
        s = str(v).strip()
        if s and s != "None":
            return s
    return None


def _height_ft(raw: str | None) -> float | None:
    try:
        return round(float(raw), 1)
    except (TypeError, ValueError):
        return None


def _features_to_points(features: list[dict]) -> list[dict]:
    """Convert NID GeoJSON features into canonical dam dicts. Non-point
    geometry is coerced to its centroid; a malformed feature is skipped
    (logged) rather than dropping the whole overlay."""
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
                "name": _pick(props, _NAME_FIELDS) or "Dam",
                "lat": float(c.y),
                "lon": float(c.x),
                "river": _pick(props, _RIVER_FIELDS),
                "owner": _pick(props, _OWNER_FIELDS),
                "city": _pick(props, _CITY_FIELDS),
                "purposes": _pick(props, _PURPOSE_FIELDS),
                "height_ft": _height_ft(_pick(props, _HEIGHT_FIELDS)),
                "year": _pick(props, _YEAR_FIELDS),
                "nid_id": _pick(props, _NID_FIELDS),
                "agency_url": NID_AGENCY_URL,
            })
        except Exception as exc:
            logger.warning("dam feature skipped: %s", exc)
    return points


def load_dams(state: str) -> list[dict]:
    """All NID dams in the state, cached per state. Returns [] if the NID
    service is unreachable -- and does NOT cache that, so the next request
    retries (there's no baseline to fall back to)."""
    if state in _dams_cache and _dams_cache[state] is not None:
        return _dams_cache[state]
    # NID_v1's STATE column carries the FULL state name ("Maryland"), not the
    # 2-letter code -- confirmed via gis-endpoint-verify. The arcgis helper
    # parses this `where` out of the URL and re-sends it encoded.
    name = (STATES.get(state) or {}).get("name")
    if not name:
        return []
    url = f"{NID_QUERY_URL}?where=STATE='{name}'&outFields=*"
    features = fetch_geojson_features(url)
    if features is None:
        logger.info("dams live feed unreachable for %s; not caching", state)
        return []
    points = _features_to_points(features)
    _dams_cache[state] = points
    return points


def dams_geojson(state: str) -> dict:
    """GeoJSON FeatureCollection for `/api/dams?state=`. One Point per dam;
    the canonical dict (minus lat/lon) travels as properties so the client
    renders the marker + popup without extra requests."""
    features: list[dict] = []
    for p in load_dams(state):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [p["lon"], p["lat"]]},
            "properties": {k: v for k, v in p.items()
                           if k not in ("lat", "lon")},
        })
    return {"type": "FeatureCollection", "features": features}
