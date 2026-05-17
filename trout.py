"""
Trout stream data from state fisheries agencies.

Loads designated trout water boundaries from ArcGIS REST endpoints for
Virginia and Maryland. Used to tag USGS gauge sites that fall on or near
trout streams and to render a distinct trout stream layer on the map.
"""

import geopandas
from shapely.geometry import Point

from arcgis import fetch_geojson_gdf

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

_trout_cache: dict[str, geopandas.GeoDataFrame | None] = {}


def load_trout_streams(state_code: str) -> geopandas.GeoDataFrame | None:
    if state_code in _trout_cache:
        return _trout_cache[state_code]

    source = TROUT_SOURCES.get(state_code)
    if not source:
        _trout_cache[state_code] = None
        return None

    gdf = fetch_geojson_gdf(source["url"])
    _trout_cache[state_code] = gdf
    return gdf


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
