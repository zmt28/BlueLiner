from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx
from datetime import datetime
from collections import defaultdict
import geopandas
import pandas as pd
import asyncio
import hashlib
import logging
import os
import re
import time

from states import STATES
from trout import load_trout_streams, is_near_trout_stream
import trout
from arcgis import USER_AGENT
import hatches
import stocking
import db


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("bluelines")

# Module-level cache (USGS daily medians, populated lazily per request)
_stats_cache: dict[str, dict] = {}

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The pins datastore must be ready before serving -- fast: runs the
    # idempotent migration against SQLite/Postgres.
    db.init_db()

    # Warm external feeds in the background so startup (and the platform
    # health check) never blocks. Both are cached + also loaded lazily, so
    # this is just a head start; the trout keyset fetch can be slow.
    async def _warm():
        try:
            await asyncio.to_thread(stocking.load_stocking, "VA")
        except Exception as exc:
            logger.warning("VA stocking warm failed: %s", exc)
        try:
            await asyncio.to_thread(load_trout_streams, "MD")
        except Exception as exc:
            logger.warning("MD trout warm failed: %s", exc)

    warm_task = asyncio.create_task(_warm())
    yield
    warm_task.cancel()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# -- Historical stats --

async def fetch_bulk_stats(site_nos: list[str]) -> None:
    """
    Fetches daily historical median discharge for multiple sites in a single
    USGS API call (the API accepts comma-separated site numbers). Populates
    _stats_cache for each site.
    """
    uncached = [s for s in site_nos if s not in _stats_cache]
    if not uncached:
        return

    url = "https://waterservices.usgs.gov/nwis/stat/"
    batch_size = 10
    # Bound total work: many batches must never make /api/gauges hang.
    # Sites not reached just lack a historical median (scoring falls back
    # to absolute thresholds) and are retried on a later request.
    deadline = time.monotonic() + 20.0

    async with httpx.AsyncClient(
        timeout=15.0, headers={"User-Agent": USER_AGENT}
    ) as client:
        for i in range(0, len(uncached), batch_size):
            if time.monotonic() > deadline:
                break
            batch = uncached[i:i + batch_size]
            params = {
                "format": "rdb",
                "sites": ",".join(batch),
                "statReportType": "daily",
                "statTypeCd": "median",
                "parameterCd": "00060",
            }
            # Initialize empty dicts for all sites in this batch
            for s in batch:
                if s not in _stats_cache:
                    _stats_cache[s] = {}

            try:
                response = await client.get(url, params=params)
                lines = response.text.strip().split("\n")
                header = None
                for line in lines:
                    if line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if header is None:
                        header = parts
                        continue
                    if len(parts) < 2 or parts[0].startswith("5s") or parts[0].startswith("-"):
                        continue
                    try:
                        row = dict(zip(header, parts))
                        site = row.get("site_no", "").strip()
                        month = int(row.get("month_nu", 0))
                        day = int(row.get("day_nu", 0))
                        val = float(row.get("p50_va", 0))
                        if site and month and day and val:
                            if site not in _stats_cache:
                                _stats_cache[site] = {}
                            _stats_cache[site][(month, day)] = val
                    except (ValueError, KeyError):
                        continue
            except Exception:
                continue


# -- Scoring --

SCORE_COLORS = {
    "green": "#2ecc71",
    "yellow": "#f39c12",
    "red": "#e74c3c",
    "gray": "#95a5a6",
}

SCORE_LABELS = {
    "green": "GOOD",
    "yellow": "FAIR",
    "red": "POOR",
    "gray": "NO DATA",
}

SCORE_BG = {
    "green": "#d5f5e3",
    "yellow": "#fef9e7",
    "red": "#fdedec",
    "gray": "#eaecee",
}


def score_conditions(variables: list[dict], historical_median: float | None = None) -> dict:
    """
    Evaluates a station's current readings for fishing suitability.

    Temperature thresholds (Fahrenheit, optimized for trout):
        Green: 48-65, Yellow: 45-48 or 65-68, Red: above 68 or below 40

    Flow scoring uses historical percentile context when available:
        Good: within 0.5x-2x of historical median
        Fair: 2x-3x or 0.25x-0.5x of median
        Poor: above 3x or below 0.25x of median
    Falls back to absolute thresholds when no historical data exists.
    """
    temp_score = None
    flow_score = None
    current_flow = None

    for var in variables:
        description = var.get("variable", "").lower()
        try:
            value = float(var.get("value", ""))
        except (ValueError, TypeError):
            continue

        if "temperature" in description and "water" in description:
            temp_f = value * 9 / 5 + 32
            if 48 <= temp_f <= 65:
                temp_score = "green"
            elif (45 <= temp_f < 48) or (65 < temp_f <= 68):
                temp_score = "yellow"
            elif temp_f > 68 or temp_f < 40:
                temp_score = "red"
            else:
                temp_score = "yellow"

        if "discharge" in description or "streamflow" in description:
            current_flow = value
            if historical_median and historical_median > 0:
                ratio = value / historical_median
                if 0.5 <= ratio <= 2.0:
                    flow_score = "green"
                elif (0.25 <= ratio < 0.5) or (2.0 < ratio <= 3.0):
                    flow_score = "yellow"
                else:
                    flow_score = "red"
            else:
                if value < 0:
                    flow_score = "red"
                elif value > 10000:
                    flow_score = "red"
                elif value > 5000:
                    flow_score = "yellow"
                else:
                    flow_score = "green"

    scores = [s for s in [temp_score, flow_score] if s is not None]
    if not scores:
        overall = "gray"
    elif "red" in scores:
        overall = "red"
    elif "yellow" in scores:
        overall = "yellow"
    else:
        overall = "green"

    return {
        "overall": overall,
        "temp": temp_score,
        "flow": flow_score,
        "current_flow": current_flow,
    }


# -- Popup HTML --

_MONTH_ABBR = "JFMAMJJASOND"
_MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]


def _month_strip_html(months: tuple, peak: tuple) -> str:
    cells = ""
    for m in range(1, 13):
        on = hatches._in_range(m, months[0], months[1])
        pk = hatches._in_range(m, peak[0], peak[1])
        if pk:
            style = "background:#27ae60;color:#fff;font-weight:700"
        elif on:
            style = "background:#d5f5e3;color:#1e8449"
        else:
            style = "background:#eef0f2;color:#aab"
        cells += (
            f'<span style="display:inline-block;width:15px;text-align:center;'
            f'font-size:9px;padding:2px 0;{style}">{_MONTH_ABBR[m - 1]}</span>'
        )
    return f'<div style="display:flex;gap:1px;margin:4px 0">{cells}</div>'


def _hatch_section_html(zone: dict | None, active: list[dict] | None,
                        month: int) -> str:
    if not zone:
        return ""
    title = f"Hatching now &mdash; {_MONTH_FULL[month - 1]} &middot; {zone['name']}"
    if not active:
        body = ('<div style="font-size:12px;color:#777">No major mayfly/caddis '
                'hatches indexed this month &mdash; fish midges, eggs, and '
                'streamers.</div>')
    else:
        body = ""
        for e in active[:6]:
            patterns = ", ".join(e["patterns"][:2])
            body += f"""
                <div style="padding:6px 0;border-top:1px solid #e3efe8">
                    <div style="font-size:13px;font-weight:700;color:#1a1a2e">{e['common_name']}
                        <span style="font-weight:400;color:#8a8a8a;font-style:italic;font-size:11px">{e['insect']}</span></div>
                    {_month_strip_html(e['months'], e['peak'])}
                    <div style="font-size:11px;color:#555">Hooks {e['hook_sizes']} &middot; {e['time_of_day']}</div>
                    <div style="font-size:11px;color:#1e8449">Try: {patterns}</div>
                </div>"""
    return f"""
        <div style="margin-top:10px;padding:8px 12px;background:#eef7f2;border:1px solid #d1f2eb;border-radius:6px">
            <div style="font-size:13px;font-weight:700;color:#0e6655">{title}</div>
            {body}
        </div>"""


def _trend_html(site_no: str | None) -> str:
    if not site_no:
        return ""
    return f"""
        <div style="margin-top:8px">
            <button type="button" class="bl-trend-btn" data-site="{site_no}"
                style="background:#eaf2fb;color:#2c6fbf;border:1px solid #b8d4f0;border-radius:6px;
                padding:5px 10px;font-size:12px;cursor:pointer">Show 1-yr flow trend</button>
            <div class="bl-trend" data-site="{site_no}" style="margin-top:6px"></div>
        </div>"""


_CHIP_TROUT = (
    '<span style="display:inline-block;padding:3px 8px;border-radius:12px;font-size:11px;'
    'font-weight:600;color:#0e6655;background:#d1f2eb;border:1px solid #1abc9c;margin-left:6px">'
    '&#x1f41f; Trout Water</span>'
)
_CHIP_STOCKED = (
    '<span style="display:inline-block;padding:3px 8px;border-radius:12px;font-size:11px;'
    'font-weight:600;color:#9c4a00;background:#fdebd0;border:1px solid #e67e22;margin-left:6px">'
    'Recently Stocked</span>'
)


def _readings_table_html(variables: list[dict]) -> str:
    rows = ""
    for i, variable in enumerate(variables):
        dt = datetime.fromisoformat(variable["dateTime"])
        bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
        rows += f"""
            <tr style="background:{bg}">
                <td style="padding:8px 10px;color:#555">{variable["variable"]}</td>
                <td style="padding:8px 10px;text-align:center;font-weight:600">{variable["value"]}</td>
                <td style="padding:8px 10px;text-align:center;color:#777;font-size:12px">{dt.strftime("%b %d, %Y at %I:%M %p")}</td>
            </tr>"""
    return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px">
            <tr style="border-bottom:2px solid #dee2e6">
                <th style="padding:6px 10px;text-align:left;color:#333;font-weight:600">Variable</th>
                <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Value</th>
                <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Updated</th>
            </tr>
            {rows}
        </table>"""


def _flow_context_html(conditions: dict, historical_median: float | None) -> str:
    if not historical_median or conditions.get("current_flow") is None:
        return ""
    current = conditions["current_flow"]
    date_label = datetime.now().strftime("%b %d")
    if current > historical_median * 1.15:
        trend, trend_color = "Above average", "#e67e22"
    elif current < historical_median * 0.85:
        trend, trend_color = "Below average", "#3498db"
    else:
        trend, trend_color = "Near median", "#27ae60"
    return f"""
        <div style="padding:8px 12px;background:#f0f4f8;border-radius:6px;margin:6px 0;font-size:13px;color:#444">
            <span style="font-weight:600">Flow context:</span>
            {current:.0f} cfs now vs. {historical_median:.0f} cfs median for {date_label}
            <span style="color:{trend_color};font-weight:600;margin-left:4px">{trend}</span>
        </div>"""


def _season_label(months: tuple) -> str:
    s, e = months
    if s == 1 and e == 12:
        return "Year-round"
    return f"{_MONTH_FULL[s - 1][:3]}–{_MONTH_FULL[e - 1][:3]}"


def _stocked_block_html(waters: list[dict]) -> str:
    if not waters:
        return ""
    items = ""
    for w in waters[:6]:
        species = ", ".join(w.get("species", []))
        link = (f'<a href="{w["agency_url"]}" target="_blank" '
                f'style="color:#2c6fbf;text-decoration:none">stocking schedule &#x2197;</a>'
                ) if w.get("agency_url") else ""
        items += f"""
            <div style="padding:5px 0;border-top:1px solid #f6e2cf">
                <div style="font-size:13px;font-weight:600;color:#1a1a2e">{w["water"]}</div>
                <div style="font-size:11px;color:#7a5230">{w.get("category", "")}
                    {("&middot; " + species) if species else ""}
                    &middot; {_season_label(w.get("season_months", (1, 12)))} {link}</div>
            </div>"""
    return f"""
        <div style="margin-top:10px;padding:8px 12px;background:#fdf3e7;border:1px solid #f6dcc0;border-radius:6px">
            <div style="font-size:13px;font-weight:700;color:#9c4a00">Stocked nearby</div>
            {items}
        </div>"""


def build_river_popup_html(river: dict) -> str:
    overall = river["overall"]
    badge_color = SCORE_COLORS[overall]
    badge_bg = SCORE_BG[overall]
    badge_label = SCORE_LABELS[overall]
    trout_html = _CHIP_TROUT if river["on_trout"] else ""
    stocked_html = _CHIP_STOCKED if river["near_stocked"] else ""

    gauges_html = ""
    for g in river["gauges"]:
        usgs = (
            f'<div style="padding:4px 0 2px;text-align:right">'
            f'<a href="https://waterdata.usgs.gov/nwis/uv?site_no={g["site_no"]}" '
            f'target="_blank" style="color:#3498db;font-size:12px;text-decoration:none">'
            f'View on USGS &#x2197;</a></div>'
        ) if g.get("site_no") else ""
        gauges_html += f"""
            <div style="border-top:1px solid #e5e7eb;padding-top:8px;margin-top:10px">
                <div style="font-size:14px;font-weight:600;color:#1a1a2e">{g["site_name"]}</div>
                {_flow_context_html(g["conditions"], g["historical_median"])}
                {_readings_table_html(g["variables"])}
                {_trend_html(g.get("site_no"))}
                {usgs}
            </div>"""

    return f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:380px">
            <div style="padding:12px 14px 8px">
                <div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-bottom:6px">{river["name"]}</div>
                <span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;
                    color:{badge_color};background:{badge_bg};border:1.5px solid {badge_color};letter-spacing:0.5px">
                    {badge_label}
                </span>{trout_html}{stocked_html}
                <div style="font-size:11px;color:#888;margin-top:5px">{len(river["gauges"])} gauge(s) on this water</div>
            </div>
            <div style="padding:6px 14px 12px">
                {_hatch_section_html(river["hatch_zone"], river["active"], river["month"])}
                {_stocked_block_html(river["stocked_waters"])}
                {gauges_html}
            </div>
        </div>
    """


# -- Helpers --

def _resolve_states(state: str) -> list[str] | None:
    # Single state only. A nationwide "all" would fan out to ~51 USGS
    # calls per request and blow the <10s budget -- broad/"near me"
    # discovery is Phase 5 (viewport loading), not a 51-state union.
    state = state.upper()
    return [state] if state in STATES else None


# USGS station names look like "GUNPOWDER FALLS NEAR GLENCOE, MD". The river
# is the part before the first locator word; "North Branch ..." stays
# distinct. Heuristic + tunable (see plan's HUC/GNIS follow-up).
_LOCATOR_RE = re.compile(r"\b(near|nr|at|abv|above|blw|below|ab|bl)\b", re.I)
_RANK = {"green": 0, "yellow": 1, "red": 2, "gray": 3}


def _river_key(site_name: str) -> tuple[str, str]:
    """(grouping_key, display_name) for a USGS station name."""
    base = re.sub(r",\s*[A-Za-z]{2}\.?\s*$", "", site_name).strip()
    head = base
    m = _LOCATOR_RE.search(base)
    if m:
        head = base[:m.start()]
    head = head.strip(" ,-.").strip() or base or site_name.strip()
    display = head.title()
    return display.lower(), display


_trout_warming: set[str] = set()


def _trout_for_state(st: str):
    """Cached trout gdf, or None while a one-shot background warm runs.

    The keyset fetch can be slow, so requests never block on it -- trout
    tags fill in once the background load caches.
    """
    if trout.is_cached(st):
        return trout.cached_streams(st)
    if st not in _trout_warming:
        _trout_warming.add(st)

        async def _warm():
            try:
                await asyncio.to_thread(load_trout_streams, st)
            except Exception as exc:
                logger.warning("trout warm failed for %s: %s", st, exc)
            finally:
                _trout_warming.discard(st)

        asyncio.create_task(_warm())
    return None


def _gdf_to_geojson_response(gdfs: list[geopandas.GeoDataFrame]) -> Response:
    valid = [g for g in gdfs if g is not None and not g.empty]
    if not valid:
        return Response(
            content='{"type":"FeatureCollection","features":[]}',
            media_type="application/json",
        )
    if len(valid) == 1:
        merged = valid[0]
    else:
        merged = geopandas.GeoDataFrame(
            pd.concat(valid, ignore_index=True), crs=valid[0].crs
        )
    return Response(content=merged.to_json(), media_type="application/json")


async def _rivers_for_states(states_to_load: list[str]) -> list[dict]:
    today = datetime.now()
    today_key = (today.month, today.day)
    month_now = today.month
    rivers: list[dict] = []

    for st in states_to_load:
        trout_gdf = _trout_for_state(st)  # non-blocking; None until warmed
        stocked_pts = await asyncio.to_thread(stocking.stocked_points, st)

        data = await get_streams(state=st)
        time_series = data.get("value", {}).get("timeSeries", [])

        # Aggregate multiple sensor readings per site
        sites = defaultdict(lambda: {"variables": [], "site_no": None})
        for series in time_series:
            source_info = series.get("sourceInfo", {})
            site_name = source_info.get("siteName", "Unknown").capitalize()
            site_no = source_info.get("siteCode", [{}])[0].get("value", "")
            geo_location = source_info.get("geoLocation", {}).get("geogLocation", {})
            latitude = geo_location.get("latitude")
            longitude = geo_location.get("longitude")
            variable_description = series.get("variable", {}).get("variableDescription")
            values_list = series.get("values", [])
            if values_list:
                value_data = values_list[0].get("value", [])
                if value_data:
                    value_entry = value_data[0]
                    key = (site_name, latitude, longitude)
                    sites[key]["variables"].append({
                        "variable": variable_description,
                        "value": value_entry.get("value"),
                        "dateTime": value_entry.get("dateTime"),
                    })
                    if site_no:
                        sites[key]["site_no"] = site_no

        discharge_site_nos = []
        for (_name, _lat, _lon), info in sites.items():
            site_no = info.get("site_no")
            has_discharge = any(
                "discharge" in v.get("variable", "").lower() or "streamflow" in v.get("variable", "").lower()
                for v in info["variables"]
            )
            if site_no and has_discharge:
                discharge_site_nos.append(site_no)

        if discharge_site_nos:
            await fetch_bulk_stats(discharge_site_nos)

        # Group sites into rivers
        groups: dict[str, dict] = {}
        for (site_name, latitude, longitude), info in sites.items():
            if not latitude or not longitude:
                continue
            variables = info["variables"]
            site_no = info.get("site_no")
            historical_median = _stats_cache.get(site_no, {}).get(today_key) if site_no else None
            conditions = score_conditions(variables, historical_median)
            on_trout = bool(
                trout_gdf is not None
                and is_near_trout_stream(latitude, longitude, trout_gdf)
            )
            key, display = _river_key(site_name)
            g = groups.setdefault(key, {
                "name": display, "lats": [], "lons": [],
                "on_trout": False, "gauges": [],
            })
            g["lats"].append(latitude)
            g["lons"].append(longitude)
            g["on_trout"] = g["on_trout"] or on_trout
            g["gauges"].append({
                "site_name": site_name, "site_no": site_no,
                "variables": variables, "conditions": conditions,
                "historical_median": historical_median,
            })

        for g in groups.values():
            clat = sum(g["lats"]) / len(g["lats"])
            clon = sum(g["lons"]) / len(g["lons"])
            overall = min(
                (gg["conditions"]["overall"] for gg in g["gauges"]),
                key=lambda o: _RANK.get(o, 3),
            )
            zone = hatches.zone_for(clat, clon)
            active = hatches.active_hatches(zone, month_now)
            stocked_waters = stocking.nearby_stocked(clat, clon, stocked_pts)
            river = {
                "name": g["name"], "lat": clat, "lon": clon,
                "overall": overall,
                "on_trout": g["on_trout"],
                "near_stocked": bool(stocked_waters),
                "hatch_zone": zone, "active": active, "month": month_now,
                "stocked_waters": stocked_waters,
                "gauges": sorted(g["gauges"], key=lambda x: x["site_name"]),
            }
            rivers.append({
                "name": river["name"],
                "lat": clat, "lon": clon,
                "conditions": {"overall": overall},
                "color": SCORE_COLORS[overall],
                "label": SCORE_LABELS[overall],
                "on_trout": river["on_trout"],
                "near_stocked": river["near_stocked"],
                "hatch_zone": zone["name"],
                "active_hatches": [e["common_name"] for e in active],
                "popup_html": build_river_popup_html(river),
            })

    return rivers


# -- Routes --

@app.head("/")
@app.get("/")
async def root():
    return RedirectResponse(url="/map?state=MD")


@app.get("/healthz")
async def healthz():
    try:
        await asyncio.to_thread(db.healthcheck)
    except Exception as exc:
        logger.error("healthcheck failed: %s", exc)
        raise HTTPException(status_code=503, detail="unhealthy")
    return {"status": "ok"}


@app.get("/streams")
async def get_streams(state: str = Query(default="MD", description="Two-letter state code (MD, VA, WV)")):
    """
    Fetches real-time stream data from the USGS NWIS instantaneous values API
    for all active monitoring sites in the specified state.
    """
    state = state.upper()
    if state not in STATES:
        return {"error": f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}"}

    api_url = "https://waterservices.usgs.gov/nwis/iv/"
    params = {
        "format": "json",
        "stateCd": STATES[state]["usgs_code"],
        "siteStatus": "active",
        "siteType": "ST,FA-WWTP,SP,ST-TS",
    }

    empty = {"value": {"timeSeries": []}}
    try:
        async with httpx.AsyncClient(
            timeout=25.0, headers={"User-Agent": USER_AGENT}
        ) as client:
            response = await client.get(api_url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        # A public app must not 500 because USGS is slow/down/rate-limiting;
        # callers treat an empty series as "no gauges right now".
        logger.warning("USGS IV fetch failed for %s: %s", state, exc)
        return empty


@app.get("/map")
async def map_shell():
    """Serves the static client shell; state/filters are resolved client-side."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/sw.js")
async def service_worker():
    # Served from root so the service worker's scope covers the whole app
    # (a /static/ path would only control /static/* requests).
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript"
    )


@app.get("/api/states")
async def api_states():
    """Supported states (code, name, map center) -- the client builds the
    selector and centering from this so states.py is the single source."""
    return [
        {"code": code, "name": info["name"], "center": info["center"]}
        for code, info in sorted(STATES.items(), key=lambda kv: kv[1]["name"])
    ]


@app.get("/api/rivers")
async def api_rivers(state: str = Query(default="MD", description="Two-letter state code.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported state: {state}. Supported: {', '.join(sorted(STATES))}",
        )
    rivers = await _rivers_for_states(states_to_load)
    return {"state": state.upper(), "rivers": rivers}


@app.get("/api/trout")
async def api_trout(state: str = Query(default="MD", description="Two-letter state code.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(status_code=400, detail=f"Unsupported state: {state}")
    # Non-blocking: cached gdf or empty until the background warm completes.
    return _gdf_to_geojson_response([_trout_for_state(st) for st in states_to_load])


@app.get("/api/history")
async def api_history(
    site_no: str = Query(..., pattern=r"^[0-9A-Za-z-]{4,20}$",
                         description="USGS site number"),
):
    """Proxies ~1 year of USGS daily values (discharge + water temp).

    History is served live from USGS, never stored locally.
    """
    url = "https://waterservices.usgs.gov/nwis/dv/"
    params = {
        "format": "json",
        "sites": site_no,
        "period": "P365D",
        "parameterCd": "00060,00010",
        "statCd": "00003",
        "siteStatus": "all",
    }
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(url, params=params)
    try:
        data = resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="USGS daily values unavailable")

    series = []
    for ts in data.get("value", {}).get("timeSeries", []):
        var = ts.get("variable", {})
        code = var.get("variableCode", [{}])[0].get("value")
        name = var.get("variableName") or var.get("variableDescription")
        unit = var.get("unit", {}).get("unitCode")
        points = []
        for v in (ts.get("values") or [{}])[0].get("value", []):
            try:
                val = float(v.get("value"))
            except (TypeError, ValueError):
                continue
            if val <= -999999:  # USGS no-data sentinel
                continue
            points.append({"date": v.get("dateTime"), "value": val})
        if points:
            series.append({"parameter": code, "name": name, "unit": unit,
                            "points": points})
    return {"site_no": site_no, "series": series}


class PinIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    note: str = Field(default="", max_length=500)


# Best-effort, per-process fixed-window limiter on the one public write
# endpoint. Not exact across gunicorn workers -- it's abuse mitigation, not
# a quota. (A shared store, e.g. Redis, would be the multi-instance answer.)
_PIN_RATE_MAX = int(os.environ.get("PIN_RATE_MAX", "20"))
_PIN_RATE_WINDOW = 60.0
_pin_hits: dict[str, tuple[float, int]] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_pins(request: Request) -> None:
    now = time.time()
    if len(_pin_hits) > 5000:  # bound memory: drop stale windows
        for k, (s, _) in list(_pin_hits.items()):
            if now - s >= _PIN_RATE_WINDOW:
                _pin_hits.pop(k, None)
    ip = _client_ip(request)
    start, count = _pin_hits.get(ip, (now, 0))
    if now - start >= _PIN_RATE_WINDOW:
        start, count = now, 0
    count += 1
    _pin_hits[ip] = (start, count)
    if count > _PIN_RATE_MAX:
        retry = int(_PIN_RATE_WINDOW - (now - start)) + 1
        raise HTTPException(
            status_code=429, detail="Too many pins, slow down.",
            headers={"Retry-After": str(retry)},
        )


def _owner(request: Request, required: bool = True) -> str | None:
    """Derive a stable owner id from the device token header.

    The client holds an opaque random token (localStorage); the server
    stores only its SHA-256, so a DB dump can't be replayed. No login,
    no server secret -- the token is an unguessable bearer capability.
    """
    token = request.headers.get("x-device-token", "").strip()
    if not (8 <= len(token) <= 200):
        if required:
            raise HTTPException(status_code=400, detail="Missing device token")
        return None
    return hashlib.sha256(token.encode()).hexdigest()


@app.get("/api/pins")
async def api_list_pins(request: Request):
    owner = _owner(request, required=False)
    if owner is None:
        return []
    return await asyncio.to_thread(db.list_pins, owner)


@app.post("/api/pins")
async def api_add_pin(pin: PinIn, request: Request):
    _rate_limit_pins(request)
    owner = _owner(request, required=True)
    return await asyncio.to_thread(db.add_pin, pin.lat, pin.lon, pin.note, owner)


@app.delete("/api/pins/{pin_id}")
async def api_delete_pin(pin_id: int, request: Request):
    owner = _owner(request, required=True)
    deleted = await asyncio.to_thread(db.delete_pin, pin_id, owner)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pin not found")
    return {"ok": True}
