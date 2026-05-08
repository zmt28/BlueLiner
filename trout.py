"""
Trout stream data from state fisheries agencies.

Loads designated trout water boundaries from ArcGIS REST endpoints for
Virginia, Maryland, and West Virginia. Used to tag USGS gauge sites that
fall on or near trout streams and to render a distinct trout stream layer
on the map.
"""

import geopandas
import httpx
from shapely.geometry import Point

TROUT_SOURCES = {
    "VA": {
        "name": "Virginia Wild Trout Streams",
        "url": (
            "https://services.dwr.virginia.gov/arcgis/rest/services/Public/"
            "WildTroutStreams/MapServer/0/query"
            "?where=1%3D1&f=geojson&outFields=NAME,CLASS,BROOK,BROWN,RAINBOW&resultRecordCount=5000"
        ),
    },
    "MD": {
        "name": "Maryland Designated Use Trout",
        "url": (
            "https://dnr.geodata.md.gov/dnrdata/rest/services/Fisheries/"
            "DesignatedUse_Trout/MapServer/0/query"
            "?where=1%3D1&f=geojson&outFields=GNIS_Name,Des_Use,STream_Nam&resultRecordCount=5000"
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

    try:
        gdf = geopandas.read_file(source["url"])
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        _trout_cache[state_code] = gdf
        return gdf
    except Exception:
        _trout_cache[state_code] = None
        return None


def is_near_trout_stream(lat: float, lon: float, trout_gdf: geopandas.GeoDataFrame,
                         buffer_deg: float = 0.002) -> bool:
    """
    Check if a point (USGS gauge) falls within ~200m of a trout stream line.
    Uses a degree-based buffer (~0.002 degrees is roughly 200m at mid-latitudes).
    """
    point = Point(lon, lat)
    buffered = point.buffer(buffer_deg)
    return bool(trout_gdf.intersects(buffered).any())
