"""
Trout stream data from state fisheries agencies.

Loads designated trout water boundaries from ArcGIS REST endpoints for
Virginia and Maryland. Used to tag USGS gauge sites that fall on or near
trout streams and to render a distinct trout stream layer on the map.
"""

import geopandas
from shapely.geometry import Point

from arcgis import fetch_geojson_gdf
from cache import LruTtl

TROUT_SOURCES = {
    "VA": {
        "name": "Virginia Wild Trout Streams",
        "url": (
            "https://services.dwr.virginia.gov/arcgis/rest/services/Public/"
            "WildTroutStreams/MapServer/0/query?where=1%3D1"
        ),
    },
    "MD": {
        "name": "Maryland Designated Use Trout",
        "url": (
            "https://dnr.geodata.md.gov/dnrdata/rest/services/Fisheries/"
            "DesignatedUse_Trout/MapServer/0/query?where=1%3D1"
        ),
    },
    # WV DNR endpoint structure is less predictable. Skipping for now --
    # can be added when a reliable GeoJSON endpoint is confirmed.
}

# Each cached gdf is the single largest runtime allocation (5-50MB of
# generalized stream lines). Bound to a handful of states, LRU-evicted --
# the key lever (after dropping to one worker) that keeps RSS under the
# 512MB cap when the viewport fans out across states.
_TROUT_CACHE_MAX = 4
_trout_cache: LruTtl = LruTtl(maxsize=_TROUT_CACHE_MAX)

# ~50m: well inside the ~450m proximity buffer, so near-stream tagging is
# unaffected, but it drops a lot of redundant vertices from the cached
# geometry.
_SIMPLIFY_TOLERANCE_DEG = 0.0005


def _slim(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Geometry-only + simplified. Tagging only ever needs the lines, so
    dropping every attribute column and decimating vertices is pure
    memory savings with no behavior change."""
    try:
        geom = gdf.geometry.simplify(_SIMPLIFY_TOLERANCE_DEG)
        return geopandas.GeoDataFrame(geometry=geom, crs=gdf.crs)
    except Exception:
        return gdf


def load_trout_streams(state_code: str) -> geopandas.GeoDataFrame | None:
    if state_code in _trout_cache:
        return _trout_cache.get(state_code)

    source = TROUT_SOURCES.get(state_code)
    if not source:
        _trout_cache[state_code] = None
        return None

    gdf = fetch_geojson_gdf(source["url"])
    if gdf is not None and not gdf.empty:
        gdf = _slim(gdf)
    _trout_cache[state_code] = gdf
    return gdf


def is_cached(state_code: str) -> bool:
    """True once a load attempt for this state has completed (cached)."""
    return state_code in _trout_cache


def cached_streams(state_code: str) -> geopandas.GeoDataFrame | None:
    """The cached gdf (or None) without triggering a blocking load."""
    return _trout_cache.get(state_code)


def is_near_trout_stream(lat: float, lon: float, trout_gdf: geopandas.GeoDataFrame,
                         buffer_deg: float = 0.005) -> bool:
    """
    Check if a point (USGS gauge) is near a trout stream line. The agency
    geometry is a generalized centerline and gauge coordinates sit slightly
    off it, so this uses an approximate ~0.005 degree (~450m) buffer. The
    value is a deliberately forgiving, tunable heuristic -- too tight a
    buffer was dropping well-known trout gauges (e.g. Gunpowder Falls).
    """
    point = Point(lon, lat)
    buffered = point.buffer(buffer_deg)
    return bool(trout_gdf.intersects(buffered).any())
