"""
Trout stocking data.

Per-state baselines of well-known stocked / specially-managed trout
waters live in `data/stocking/<STATE>.json` (in-memory, zero network).
Baseline coordinates are approximate access points -- precise enough
for the ~1 km proximity tag, and they guarantee famous waters
(e.g. Gunpowder Falls) are surfaced even if a state's wild-trout layer
is incomplete.

Live state-agency ArcGIS overlays are declared in
`data/stocking/sources.json` (one entry per layer; a state may have
several). Each entry carries the verified query URL plus per-source
field mappings -- agencies disagree wildly on schema (VA encodes
species as 0/1 flag columns, others use a free-text field). The loader
degrades gracefully to the baseline when an endpoint is unreachable.

Point shape: {water, lat, lon, species[], category, season_months (s,e),
agency_url, source}
"""

import json
import logging
import os
import re

from shapely.geometry import shape

from arcgis import fetch_geojson_features
from cache import LruTtl

logger = logging.getLogger("blueliner.stocking")

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "stocking")


def _baseline_states() -> list[str]:
    """States with a bundled data/stocking/<STATE>.json baseline file."""
    if not os.path.isdir(_DATA_DIR):
        return []
    return sorted(
        fn[:-5] for fn in os.listdir(_DATA_DIR)
        if fn.endswith(".json") and len(fn) == 7 and fn[:2].isupper()
    )


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
    state: _load_baseline(state) for state in _baseline_states()
}

MD_DNR_URL = "https://dnr.maryland.gov/fisheries/pages/trout/stocking.aspx"
VA_DWR_URL = "https://dwr.virginia.gov/fishing/trout-stocking-schedule/"
WV_DNR_URL = "https://wvdnr.gov/fishing/trout-stocking/"
PA_PFBC_URL = "https://www.fishandboat.com/Fish/Trout/Pages/default.aspx"


def _load_sources() -> dict[str, list[dict]]:
    """Read the declarative live-feed registry. Each source dict:
    {state, label, url, agency_url, category?, name_field?, species_field?,
     species_flags?, season_months?, dedupe?}. Grouped by state; a state
    may declare several layers (e.g. stocked lakes + stocked reaches)."""
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


STOCKING_SOURCES: dict[str, list[dict]] = _load_sources()

_NAME_FIELDS = ("WATER", "Water", "WATERBODY", "Waterbody", "STREAM",
                "Stream_Nam", "NAME", "Name", "GNIS_Name")
_SPECIES_FIELDS = ("SPECIES", "Species", "TROUT_SPEC")
_CATEGORY_FIELDS = ("CATEGORY", "Category", "TYPE", "Type", "CATEGO")
_SEASON_FIELDS = ("SEASON_MONTHS", "SeasonMonths", "STOCK_MONTHS", "Season")

_MONTHS_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                "Sep", "Oct", "Nov", "Dec")

# All states can now carry live feeds, so size for the whole catalog and
# expire entries so a day-stale feed (or a transient fetch failure that
# cached baseline-only) refreshes without a restart.
_stocking_cache: LruTtl = LruTtl(maxsize=64, ttl=6 * 3600)


def _pick(props: dict, fields: tuple[str, ...]) -> str | None:
    for f in fields:
        v = props.get(f)
        if v not in (None, ""):
            return str(v)
    return None


def _season_from_props(props: dict) -> tuple[int, int]:
    """Best-effort (start, end) month from the feed; (1, 12) when absent or
    unparseable. The feed schema isn't pinned down, so this stays defensive --
    it only trusts a field that clearly yields two valid month numbers."""
    raw = _pick(props, _SEASON_FIELDS)
    if raw:
        nums = [int(n) for n in re.findall(r"\d{1,2}", raw)]
        if len(nums) >= 2 and 1 <= nums[0] <= 12 and 1 <= nums[1] <= 12:
            return (nums[0], nums[1])
    return (1, 12)


def _season_label(months: tuple[int, int]) -> str:
    s, e = months
    if s == 1 and e == 12:
        return "Year-round"
    return f"{_MONTHS_ABBR[s - 1]}–{_MONTHS_ABBR[e - 1]}"


_TRUTHY = (1, "1", True, "Yes", "YES", "yes", "Y", "y", "true", "True")


def _truthy(v) -> bool:
    """Flag-column truthiness: Y/Yes/1/true strings, plus positive
    numbers (agencies like WDFW publish counts, e.g. BoatRamps=2)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return v in _TRUTHY


def _species_from_props(props: dict, src: dict) -> list[str]:
    """Species list via the source's mapping: `species_flags` maps 0/1
    flag columns to labels (VA style); `species_field` names a free-text
    column; otherwise fall back to the generic candidates."""
    flags = src.get("species_flags")
    if flags:
        return [label for field, label in flags.items()
                if _truthy(props.get(field))]
    field = src.get("species_field")
    raw = props.get(field) if field else _pick(props, _SPECIES_FIELDS)
    if raw in (None, ""):
        return []
    return [s.strip() for s in re.split(r"[,/;&]", str(raw)) if s.strip()]


def _features_to_points(features: list[dict], src: dict) -> list[dict]:
    agency_url = src.get("agency_url") or src.get("url")
    name_field = src.get("name_field")
    default_season = tuple(src.get("season_months") or ()) or None
    points: list[dict] = []
    seen: dict[tuple, dict] = {}
    skipped = 0
    for f in features:
        try:
            g = f.get("geometry")
            if not g:
                skipped += 1
                continue
            geom = shape(g)
            if geom.is_empty:
                skipped += 1
                continue
            c = geom.centroid
            props = f.get("properties") or {}
            water = ((str(props.get(name_field)) if props.get(name_field)
                      else None) if name_field
                     else _pick(props, _NAME_FIELDS)) or "Stocked water"
            if src.get("dedupe"):
                # One pin per named water per ~0.1 deg cell. Collapses
                # multi-segment reach layers (and per-event stocking
                # records) without merging same-named creeks in
                # different corners of the state. Species union across
                # collapsed records so a water stocked with browns in
                # March and rainbows in May shows both.
                key = (water.strip().lower(),
                       round(float(c.y), 1), round(float(c.x), 1))
                prior = seen.get(key)
                if prior is not None:
                    for s in _species_from_props(props, src):
                        if s not in prior["species"]:
                            prior["species"].append(s)
                    continue
            points.append({
                "water": water,
                "lat": float(c.y),
                "lon": float(c.x),
                "species": _species_from_props(props, src),
                "category": (_pick(props, _CATEGORY_FIELDS)
                             if src.get("category_from_props") else None)
                            or src.get("category") or "Stocked water",
                "season_months": default_season or _season_from_props(props),
                "agency_url": agency_url,
            })
            if src.get("dedupe"):
                seen[key] = points[-1]
        except Exception as exc:
            # Don't let one malformed feature drop the whole overlay -- but
            # surface it (the old silent pass hid feed-schema drift).
            skipped += 1
            logger.warning("stocking feature skipped: %s", exc)
    if skipped:
        logger.info("stocking live feed: kept %d, skipped %d",
                    len(points), skipped)
    return points


def load_stocking(state: str) -> list[dict]:
    """Baseline points for the state, plus any live overlays that respond."""
    if state in _stocking_cache and _stocking_cache[state] is not None:
        return _stocking_cache[state]

    points = [dict(p, source="baseline") for p in STOCKING_BASELINE.get(state, [])]

    for src in STOCKING_SOURCES.get(state, []):
        features = fetch_geojson_features(src["url"])
        if features:
            for p in _features_to_points(features, src):
                points.append(dict(p, source="live"))
        else:
            logger.info("stocking live feed unreachable: %s",
                        src.get("label", src["url"]))

    _stocking_cache[state] = points
    return points


def stocked_points(state: str) -> list[dict]:
    return load_stocking(state)


def stocking_geojson(state: str) -> dict:
    """GeoJSON FeatureCollection for `/api/stocking?state=`. One Point per
    stocked water; the canonical fields travel as properties (season as a
    pre-formatted label) so the client renders pins + popups directly."""
    features: list[dict] = []
    for p in load_stocking(state):
        sm = p.get("season_months") or (1, 12)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [p["lon"], p["lat"]]},
            "properties": {
                "water": p.get("water"),
                "species": p.get("species") or [],
                "category": p.get("category"),
                "season": _season_label(tuple(sm)),
                "agency_url": p.get("agency_url"),
                "source": p.get("source"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


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
