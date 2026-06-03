"""Geographic relevance gate: does a candidate layer actually cover the state?

The Phase-0 run latched a Great Smoky Mountains (TN) point layer onto a
*Colorado* search -- a fuzzy ArcGIS text match handed us an out-of-state layer.
The fix: reject any candidate whose geographic extent doesn't intersect the
target state's bounding box. A border layer (GRSM straddles TN/NC) intersects
both and is kept for both; a Smokies layer can never satisfy CO.

`extent_intersects` takes a WGS84 extent (probe.py asks ArcGIS for the extent
in outSR=4326, so any source CRS is normalized server-side; `to_wgs84` is the
fallback for the few servers that ignore that). Pure -- unit-tested offline.
"""
from __future__ import annotations

import math

# Approximate, lightly padded WGS84 bounding boxes: (lon_min, lat_min, lon_max, lat_max).
STATE_BBOX = {
    "AL": (-88.6, 30.1, -84.8, 35.1), "AZ": (-115.0, 31.2, -108.9, 37.1),
    "AR": (-94.7, 32.9, -89.6, 36.6), "CA": (-124.6, 32.4, -114.0, 42.1),
    "CO": (-109.2, 36.9, -101.9, 41.1), "CT": (-73.8, 40.9, -71.7, 42.1),
    "DE": (-75.9, 38.4, -74.9, 39.9), "FL": (-87.7, 24.3, -79.9, 31.1),
    "GA": (-85.7, 30.3, -80.7, 35.1), "ID": (-117.3, 41.9, -110.9, 49.1),
    "IL": (-91.6, 36.9, -87.0, 42.6), "IN": (-88.2, 37.7, -84.7, 41.8),
    "IA": (-96.7, 40.3, -90.1, 43.6), "KS": (-102.1, 36.9, -94.5, 40.1),
    "KY": (-89.7, 36.4, -81.9, 39.2), "LA": (-94.1, 28.8, -88.7, 33.1),
    "ME": (-71.2, 42.9, -66.9, 47.6), "MD": (-79.5, 37.8, -74.9, 39.8),
    "MA": (-73.6, 41.1, -69.8, 42.9), "MI": (-90.5, 41.6, -82.3, 48.4),
    "MN": (-97.3, 43.4, -89.4, 49.5), "MS": (-91.7, 30.1, -88.0, 35.1),
    "MO": (-95.8, 35.9, -89.0, 40.7), "MT": (-116.2, 44.3, -103.9, 49.1),
    "NE": (-104.1, 39.9, -95.2, 43.1), "NV": (-120.1, 34.9, -113.9, 42.1),
    "NH": (-72.6, 42.6, -70.5, 45.4), "NJ": (-75.6, 38.8, -73.8, 41.4),
    "NM": (-109.1, 31.2, -102.9, 37.1), "NY": (-79.9, 40.4, -71.8, 45.1),
    "NC": (-84.4, 33.7, -75.4, 36.7), "ND": (-104.1, 45.9, -96.5, 49.1),
    "OH": (-85.0, 38.3, -80.5, 42.1), "OK": (-103.1, 33.6, -94.4, 37.1),
    "OR": (-124.7, 41.9, -116.4, 46.4), "PA": (-80.6, 39.6, -74.6, 42.4),
    "RI": (-71.9, 41.1, -71.1, 42.1), "SC": (-83.4, 32.0, -78.5, 35.3),
    "SD": (-104.1, 42.4, -96.4, 46.0), "TN": (-90.4, 34.9, -81.6, 36.7),
    "TX": (-106.7, 25.8, -93.5, 36.6), "UT": (-114.1, 36.9, -108.9, 42.1),
    "VT": (-73.5, 42.7, -71.4, 45.1), "VA": (-83.7, 36.5, -75.1, 39.5),
    "WA": (-124.9, 45.5, -116.9, 49.1), "WV": (-82.7, 37.1, -77.7, 40.7),
    "WI": (-92.9, 42.4, -86.8, 47.1), "WY": (-111.1, 40.9, -103.9, 45.1),
}

_MERC_R = 20037508.342789244


def to_wgs84(ext: dict) -> dict | None:
    """Best-effort normalize an ArcGIS extent dict to lon/lat. Handles 4326
    pass-through and 3857/102100 Web Mercator; returns None for SRs we can't
    convert (caller then can't gate on geography and keeps the candidate)."""
    if not ext or "xmin" not in ext:
        return None
    wkid = (ext.get("spatialReference") or {}).get("latestWkid") \
        or (ext.get("spatialReference") or {}).get("wkid")
    if wkid in (None, 4326):
        return {k: ext[k] for k in ("xmin", "ymin", "xmax", "ymax")}
    if wkid in (3857, 102100):
        def lon(x): return x / _MERC_R * 180.0
        def lat(y):
            return math.degrees(2 * math.atan(math.exp(y / _MERC_R * math.pi)) - math.pi / 2)
        return {"xmin": lon(ext["xmin"]), "ymin": lat(ext["ymin"]),
                "xmax": lon(ext["xmax"]), "ymax": lat(ext["ymax"])}
    return None


def extent_intersects(ext_wgs84: dict | None, state: str, pad: float = 0.25) -> bool:
    """True if a WGS84 extent overlaps the state's (padded) bbox. Unknown extent
    or unknown state -> True (don't drop what we can't verify)."""
    box = STATE_BBOX.get(state.upper())
    if not box or not ext_wgs84:
        return True
    lon0, lat0, lon1, lat1 = box
    return not (ext_wgs84["xmax"] < lon0 - pad or ext_wgs84["xmin"] > lon1 + pad
                or ext_wgs84["ymax"] < lat0 - pad or ext_wgs84["ymin"] > lat1 + pad)
