"""
Trout stocking data.

Per-state baselines of well-known stocked / specially-managed trout
waters live in `data/stocking/<STATE>.json` (in-memory, zero network).
For VA we also overlay the live VA DWR ArcGIS feed when reachable.
Baseline coordinates are approximate access points -- precise enough
for the ~1 km proximity tag, and they guarantee famous waters
(e.g. Gunpowder Falls) are surfaced even if a state's wild-trout layer
is incomplete.

Point shape: {water, lat, lon, species[], category, season_months (s,e),
agency_url, source}
"""

import json
import os

from arcgis import fetch_geojson_gdf
from cache import LruTtl

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "stocking")


def _load_baseline(state: str) -> list[dict]:
    """Read data/stocking/<STATE>.json and coerce season_months to a tuple.
    Returns [] if the file is absent -- many states legitimately have none."""
    path = os.path.join(_DATA_DIR, f"{state}.json")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        rows = json.load(f)
    out: list[dict] = []
    for r in rows:
        if "season_months" in r and isinstance(r["season_months"], list):
            r = dict(r, season_months=tuple(r["season_months"]))
        out.append(r)
    return out


# Loaded once at import (cheap, in-memory, <100 entries per state).
STOCKING_BASELINE: dict[str, list[dict]] = {
    state: _load_baseline(state)
    for state in ("MD", "VA", "WV", "PA")
}

MD_DNR_URL = "https://dnr.maryland.gov/fisheries/pages/trout/stocking.aspx"
VA_DWR_URL = "https://dwr.virginia.gov/fishing/trout-stocking-schedule/"
WV_DNR_URL = "https://wvdnr.gov/fishing/trout-stocking/"
PA_PFBC_URL = "https://www.fishandboat.com/Fish/Trout/Pages/default.aspx"

# Live overlay. The exact VA DWR stocking REST URL is not verifiable from
# this environment; the loader degrades gracefully to the baseline if the
# endpoint is wrong or unreachable.
STOCKING_SOURCES = {
    "VA": {
        "name": "VA DWR Trout Stocking",
        "url": (
            "https://services.dwr.virginia.gov/arcgis/rest/services/Public/"
            "TroutStocking/MapServer/0/query?where=1%3D1"
        ),
    },
}

_NAME_FIELDS = ("WATER", "Water", "WATERBODY", "Waterbody", "STREAM",
                "Stream_Nam", "NAME", "Name", "GNIS_Name")
_SPECIES_FIELDS = ("SPECIES", "Species", "TROUT_SPEC")
_CATEGORY_FIELDS = ("CATEGORY", "Category", "TYPE", "Type", "CATEGO")

_stocking_cache: LruTtl = LruTtl(maxsize=8)


def _pick(props: dict, fields: tuple[str, ...]) -> str | None:
    for f in fields:
        v = props.get(f)
        if v not in (None, ""):
            return str(v)
    return None


def _gdf_to_points(gdf, agency_url: str) -> list[dict]:
    points: list[dict] = []
    for _, row in gdf.iterrows():
        try:
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            c = geom.centroid
            props = {k: row[k] for k in gdf.columns if k != "geometry"}
            species = _pick(props, _SPECIES_FIELDS)
            points.append({
                "water": _pick(props, _NAME_FIELDS) or "Stocked water",
                "lat": float(c.y),
                "lon": float(c.x),
                "species": [s.strip() for s in species.split(",")] if species else [],
                "category": _pick(props, _CATEGORY_FIELDS) or "Stocked (VA DWR)",
                "season_months": (1, 12),
                "agency_url": agency_url,
            })
        except Exception:
            continue
    return points


def load_stocking(state: str) -> list[dict]:
    """Baseline points for the state, plus the live VA overlay when available."""
    if state in _stocking_cache and _stocking_cache[state] is not None:
        return _stocking_cache[state]

    points = [dict(p, source="baseline") for p in STOCKING_BASELINE.get(state, [])]

    source = STOCKING_SOURCES.get(state)
    if source:
        gdf = fetch_geojson_gdf(source["url"])
        if gdf is not None and not gdf.empty:
            for p in _gdf_to_points(gdf, VA_DWR_URL):
                points.append(dict(p, source="live"))

    _stocking_cache[state] = points
    return points


def stocked_points(state: str) -> list[dict]:
    return load_stocking(state)


def nearby_stocked(lat: float, lon: float, points: list[dict],
                   buffer_deg: float = 0.02) -> list[dict]:
    """Stocked waters within ~buffer_deg (~2 km), nearest first."""
    b2 = buffer_deg * buffer_deg
    hits = []
    for p in points:
        d2 = (lat - p["lat"]) ** 2 + (lon - p["lon"]) ** 2
        if d2 <= b2:
            hits.append((d2, p))
    hits.sort(key=lambda h: h[0])
    return [p for _, p in hits]


def is_near_stocked(lat: float, lon: float, points: list[dict],
                    buffer_deg: float = 0.02) -> bool:
    """True if any stocked point is within ~buffer_deg (~2 km) of the gauge."""
    return bool(nearby_stocked(lat, lon, points, buffer_deg))
