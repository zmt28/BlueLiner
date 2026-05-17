"""
Trout stocking data.

A bundled, curated baseline of well-known stocked / specially-managed trout
waters for MD, VA, and WV (in-memory, zero network), overlaid for VA with
the live VA DWR ArcGIS feed when reachable. Baseline coordinates are
approximate access points -- precise enough for the ~1 km proximity tag, and
they guarantee famous waters (e.g. Gunpowder Falls) are surfaced even if a
state's wild-trout layer is incomplete.

Point shape: {water, lat, lon, species[], category, season_months (s,e),
agency_url, source}
"""

from arcgis import fetch_geojson_gdf

MD_DNR_URL = "https://dnr.maryland.gov/fisheries/pages/trout/stocking.aspx"
VA_DWR_URL = "https://dwr.virginia.gov/fishing/trout-stocking-schedule/"
WV_DNR_URL = "https://wvdnr.gov/fishing/trout-stocking/"

STOCKING_BASELINE: dict[str, list[dict]] = {
    "MD": [
        # The Gunpowder Falls trophy tailwater runs ~13 mi from Prettyboy Dam
        # to Loch Raven. Several points along the reach so gauges anywhere on
        # it get tagged (a single point + ~2 km buffer cannot cover it).
        {"water": "Gunpowder Falls (Prettyboy tailwater)", "lat": 39.6116,
         "lon": -76.7290, "species": ["Brown", "Rainbow"],
         "category": "Tailwater - wild + stocked", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Gunpowder Falls (Falls Rd / Masemore)", "lat": 39.6361,
         "lon": -76.6889, "species": ["Brown", "Rainbow"],
         "category": "Tailwater - wild + stocked", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Gunpowder Falls (Glencoe / Monkton)", "lat": 39.5760,
         "lon": -76.6130, "species": ["Brown", "Rainbow"],
         "category": "Tailwater - wild + stocked", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Gunpowder Falls (Phoenix / Loch Raven)", "lat": 39.5180,
         "lon": -76.5950, "species": ["Brown", "Rainbow"],
         "category": "Tailwater - wild + stocked", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Big Hunting Creek", "lat": 39.6206, "lon": -77.4569,
         "species": ["Brook", "Brown", "Rainbow"],
         "category": "Fly-fishing-only - catch & return", "season_months": (3, 11),
         "agency_url": MD_DNR_URL},
        {"water": "Morgan Run", "lat": 39.4007, "lon": -77.0006,
         "species": ["Brown", "Rainbow"], "category": "Delayed harvest",
         "season_months": (10, 5), "agency_url": MD_DNR_URL},
        {"water": "Patapsco River (Avalon / Daniels)", "lat": 39.2510,
         "lon": -76.7820, "species": ["Rainbow", "Brown"],
         "category": "Put-and-take", "season_months": (3, 5),
         "agency_url": MD_DNR_URL},
        {"water": "North Branch Potomac (Barnum)", "lat": 39.4869,
         "lon": -79.1003, "species": ["Brown", "Rainbow", "Cutthroat"],
         "category": "Trophy tailwater", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Savage River Tailwater", "lat": 39.5036, "lon": -79.1206,
         "species": ["Brown", "Brook"], "category": "Trophy - special regs",
         "season_months": (1, 12), "agency_url": MD_DNR_URL},
        {"water": "Beaver Creek (Washington Co.)", "lat": 39.5446,
         "lon": -77.6386, "species": ["Brown", "Rainbow"],
         "category": "Limestone - special regs", "season_months": (1, 12),
         "agency_url": MD_DNR_URL},
        {"water": "Owens Creek", "lat": 39.6600, "lon": -77.4500,
         "species": ["Rainbow", "Brown"], "category": "Put-and-take",
         "season_months": (3, 5), "agency_url": MD_DNR_URL},
    ],
    "VA": [
        {"water": "Mossy Creek", "lat": 38.3593, "lon": -78.8569,
         "species": ["Brown"], "category": "Trophy fly-fishing-only",
         "season_months": (1, 12), "agency_url": VA_DWR_URL},
        {"water": "Smith River (below Philpott)", "lat": 36.7790,
         "lon": -80.0179, "species": ["Brown", "Rainbow"],
         "category": "Trophy tailwater", "season_months": (1, 12),
         "agency_url": VA_DWR_URL},
        {"water": "Jackson River (Hidden Valley)", "lat": 37.9100,
         "lon": -79.8500, "species": ["Brown", "Rainbow"],
         "category": "Tailwater - special regs", "season_months": (1, 12),
         "agency_url": VA_DWR_URL},
        {"water": "South River (Waynesboro)", "lat": 38.0696, "lon": -78.8889,
         "species": ["Brown", "Rainbow"], "category": "Special regulation",
         "season_months": (1, 12), "agency_url": VA_DWR_URL},
        {"water": "Big Tumbling Creek (Clinch Mtn.)", "lat": 36.8800,
         "lon": -81.7600, "species": ["Rainbow", "Brown", "Brook"],
         "category": "Fee fishing - heavily stocked", "season_months": (4, 9),
         "agency_url": VA_DWR_URL},
        {"water": "Back Creek (below Gathright)", "lat": 38.0600,
         "lon": -79.9200, "species": ["Brown", "Rainbow"],
         "category": "Tailwater", "season_months": (1, 12),
         "agency_url": VA_DWR_URL},
    ],
    "WV": [
        {"water": "Elk River (below Sutton Dam)", "lat": 38.5100,
         "lon": -80.5400, "species": ["Brown", "Rainbow", "Golden"],
         "category": "Trophy tailwater", "season_months": (1, 12),
         "agency_url": WV_DNR_URL},
        {"water": "Williams River", "lat": 38.3500, "lon": -80.4200,
         "species": ["Brook", "Brown", "Rainbow"],
         "category": "Stocked + catch & release", "season_months": (3, 11),
         "agency_url": WV_DNR_URL},
        {"water": "Cranberry River", "lat": 38.2800, "lon": -80.4500,
         "species": ["Brook", "Rainbow"], "category": "Stocked wilderness",
         "season_months": (3, 11), "agency_url": WV_DNR_URL},
        {"water": "North Fork South Branch (Smoke Hole)", "lat": 38.8300,
         "lon": -79.2700, "species": ["Brown", "Rainbow", "Golden"],
         "category": "Stocked", "season_months": (2, 6),
         "agency_url": WV_DNR_URL},
        {"water": "Seneca Creek", "lat": 38.8300, "lon": -79.3800,
         "species": ["Brook", "Rainbow"], "category": "Stocked",
         "season_months": (3, 6), "agency_url": WV_DNR_URL},
        {"water": "Shavers Fork (Cheat)", "lat": 38.7000, "lon": -79.8500,
         "species": ["Brown", "Rainbow", "Brook"],
         "category": "Stocked + native", "season_months": (1, 12),
         "agency_url": WV_DNR_URL},
    ],
}

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

_stocking_cache: dict[str, list[dict] | None] = {}


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


def is_near_stocked(lat: float, lon: float, points: list[dict],
                    buffer_deg: float = 0.02) -> bool:
    """True if any stocked point is within ~buffer_deg (~2 km) of the gauge."""
    b2 = buffer_deg * buffer_deg
    for p in points:
        dlat = lat - p["lat"]
        dlon = lon - p["lon"]
        if dlat * dlat + dlon * dlon <= b2:
            return True
    return False
