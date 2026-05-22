"""
Trout stream data from state fisheries agencies.

Loads designated trout water boundaries from ArcGIS REST endpoints for
Virginia and Maryland. Used to tag USGS gauge sites that fall on or near
trout streams and to render a distinct trout stream layer on the map.

Shapely-only (no geopandas) -- importing geopandas costs ~83 MB of RSS,
which the 512 MB free tier can't spare. A per-state `TroutLayer` keeps the
simplified GeoJSON features (for serving the map layer) plus a lazily-built
`shapely.STRtree` (for the per-gauge proximity test).
"""

from shapely import STRtree
from shapely.geometry import Point, mapping, shape

from arcgis import fetch_geojson_features
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
    # TODO PA -- Pennsylvania Fish & Boat Commission publishes Class A
    # Wild Trout Streams. When a stable ArcGIS query URL is confirmed,
    # add:
    #   "PA": {"name": "PA Class A Wild Trout Streams (PFBC)",
    #          "url": "https://<verified-pfbc-arcgis>/query?where=1%3D1"},
    # Until then, PA gauges still load + serve fine; they just don't get
    # the trout-water tag.
    #
    # TODO WV -- WV DEP/DNR endpoint history has been unreliable. The
    # `data/trout/WV.json` bundled-fallback pattern (see
    # data/trout/README.md) is the safer path; we'll wire it up once we
    # have a vetted GeoJSON for WV.
}

# Each cached layer is the single largest runtime allocation (a few MB of
# generalized stream lines + an STRtree). Bound to a handful of states,
# LRU-evicted -- the lever that keeps RSS in check when the viewport fans
# out across states.
_TROUT_CACHE_MAX = 4
_trout_cache: LruTtl = LruTtl(maxsize=_TROUT_CACHE_MAX)

# ~50m: well inside the ~450m proximity buffer, so near-stream tagging is
# unaffected, but it drops a lot of redundant vertices.
_SIMPLIFY_TOLERANCE_DEG = 0.0005


class TroutLayer:
    """Simplified trout-stream geometry for one state. `features` are
    geometry-only GeoJSON dicts (for `/api/trout`); the STRtree over their
    shapely geometries is built lazily on the first proximity query."""

    __slots__ = ("features", "_geoms", "_tree", "_built")

    def __init__(self, features: list[dict]):
        self.features = features
        self._geoms: list = []
        self._tree = None
        self._built = False

    def _ensure_tree(self):
        if self._built:
            return
        self._built = True
        try:
            self._geoms = [shape(f["geometry"]) for f in self.features]
            self._tree = STRtree(self._geoms) if self._geoms else None
        except Exception:
            self._geoms, self._tree = [], None

    def near(self, lat: float, lon: float, buffer_deg: float = 0.005) -> bool:
        self._ensure_tree()
        if self._tree is None:
            return False
        buffered = Point(lon, lat).buffer(buffer_deg)
        # query() prunes by bbox; predicate="intersects" confirms true
        # geometric intersection. Returns matching geometry indices.
        return len(self._tree.query(buffered, predicate="intersects")) > 0


def _slim_features(features: list[dict]) -> list[dict]:
    """Geometry-only + simplified GeoJSON features. Tagging + the map
    layer only need the lines; dropping attributes and decimating
    vertices is pure memory savings with no behavior change."""
    out: list[dict] = []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        try:
            geom = shape(g).simplify(_SIMPLIFY_TOLERANCE_DEG)
        except Exception:
            continue
        if geom.is_empty:
            continue
        out.append({"type": "Feature", "geometry": mapping(geom),
                    "properties": {}})
    return out


def load_trout_streams(state_code: str) -> "TroutLayer | None":
    if state_code in _trout_cache:
        return _trout_cache.get(state_code)

    source = TROUT_SOURCES.get(state_code)
    if not source:
        _trout_cache[state_code] = None
        return None

    features = fetch_geojson_features(source["url"])
    layer = TroutLayer(_slim_features(features)) if features else None
    _trout_cache[state_code] = layer
    return layer


def is_cached(state_code: str) -> bool:
    """True once a load attempt for this state has completed (cached)."""
    return state_code in _trout_cache


def cached_streams(state_code: str) -> "TroutLayer | None":
    """The cached layer (or None) without triggering a blocking load."""
    return _trout_cache.get(state_code)


def is_near_trout_stream(lat: float, lon: float, layer: "TroutLayer | None",
                         buffer_deg: float = 0.005) -> bool:
    """
    Check if a point (USGS gauge) is near a trout stream line. The agency
    geometry is a generalized centerline and gauge coordinates sit slightly
    off it, so this uses an approximate ~0.005 degree (~450m) buffer -- a
    deliberately forgiving, tunable heuristic (too tight a buffer dropped
    well-known trout gauges like Gunpowder Falls).
    """
    if layer is None:
        return False
    return layer.near(lat, lon, buffer_deg)
