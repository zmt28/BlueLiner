from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import httpx
from datetime import datetime
from collections import defaultdict
import geopandas
import pandas as pd
import asyncio
import os

from states import STATES, get_linear_urls, get_area_urls
from trout import load_trout_streams, is_near_trout_stream
import db


# Module-level caches populated at startup
_shapefile_cache: dict[str, geopandas.GeoDataFrame] = {}
_stats_cache: dict[str, dict] = {}

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _load_shapefiles_for_state(state_code: str) -> geopandas.GeoDataFrame:
    """Load TIGER shapefiles for a state, merge into one GeoDataFrame, and simplify."""
    urls = get_linear_urls(state_code)
    # Only load area water for small states -- lakes/ponds add bulk but little
    # value for stream fishing
    if len(STATES[state_code]["fips_codes"]) < 30:
        urls += get_area_urls(state_code)

    gdfs = []
    for url in urls:
        try:
            gdfs.append(geopandas.read_file(url))
        except Exception:
            continue

    if not gdfs:
        return geopandas.GeoDataFrame()

    merged = pd.concat(gdfs, ignore_index=True)
    merged = merged[merged["FULLNAME"].notna() & (merged["FULLNAME"] != "")]
    merged["geometry"] = merged["geometry"].simplify(
        tolerance=0.001, preserve_topology=True
    )
    return merged


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the user-content datastore (saved pins).
    db.init_db()
    # Pre-load Maryland shapefiles at startup so the first request is fast.
    # Other states load on first request.
    _shapefile_cache["MD"] = await asyncio.to_thread(_load_shapefiles_for_state, "MD")
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_cached_shapefiles(state_code: str) -> geopandas.GeoDataFrame:
    if state_code not in _shapefile_cache:
        _shapefile_cache[state_code] = _load_shapefiles_for_state(state_code)
    return _shapefile_cache[state_code]


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

    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(uncached), batch_size):
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

def build_popup_html(site_name: str, variables: list[dict], conditions: dict,
                     historical_median: float | None, on_trout: bool = False,
                     site_no: str | None = None) -> str:
    score = conditions["overall"]
    badge_color = SCORE_COLORS[score]
    badge_bg = SCORE_BG[score]
    badge_label = SCORE_LABELS[score]
    trout_html = (
        '<span style="display:inline-block;padding:3px 8px;border-radius:12px;font-size:11px;'
        'font-weight:600;color:#0e6655;background:#d1f2eb;border:1px solid #1abc9c;margin-left:6px">'
        '&#x1f41f; Trout Water</span>'
    ) if on_trout else ""

    rows = ""
    for i, variable in enumerate(variables):
        desc = variable["variable"]
        val = variable["value"]
        dt = datetime.fromisoformat(variable["dateTime"])
        formatted_dt = dt.strftime("%b %d, %Y at %I:%M %p")
        bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
        rows += f"""
            <tr style="background:{bg}">
                <td style="padding:8px 10px;color:#555">{desc}</td>
                <td style="padding:8px 10px;text-align:center;font-weight:600">{val}</td>
                <td style="padding:8px 10px;text-align:center;color:#777;font-size:12px">{formatted_dt}</td>
            </tr>
        """

    # Historical flow context line
    flow_context = ""
    if historical_median and conditions.get("current_flow") is not None:
        current = conditions["current_flow"]
        today = datetime.now()
        date_label = today.strftime("%b %d")
        if current > historical_median * 1.15:
            trend = "Above average"
            trend_color = "#e67e22"
        elif current < historical_median * 0.85:
            trend = "Below average"
            trend_color = "#3498db"
        else:
            trend = "Near median"
            trend_color = "#27ae60"
        flow_context = f"""
            <div style="padding:8px 12px;background:#f0f4f8;border-radius:6px;margin-bottom:10px;font-size:13px;color:#444">
                <span style="font-weight:600">Flow context:</span>
                {current:.0f} cfs now vs. {historical_median:.0f} cfs median for {date_label}
                <span style="color:{trend_color};font-weight:600;margin-left:4px">{trend}</span>
            </div>
        """

    return f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;min-width:380px">
            <div style="padding:12px 14px 8px">
                <div style="font-size:18px;font-weight:700;color:#1a1a2e;margin-bottom:6px">{site_name}</div>
                <span style="display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:700;
                    color:{badge_color};background:{badge_bg};border:1.5px solid {badge_color};letter-spacing:0.5px">
                    {badge_label}
                </span>{trout_html}
            </div>
            <div style="padding:6px 14px 10px">
                {flow_context}
                <table style="width:100%;border-collapse:collapse;font-size:13px">
                    <tr style="border-bottom:2px solid #dee2e6">
                        <th style="padding:6px 10px;text-align:left;color:#333;font-weight:600">Variable</th>
                        <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Value</th>
                        <th style="padding:6px 10px;text-align:center;color:#333;font-weight:600">Updated</th>
                    </tr>
                    {rows}
                </table>
                {f'<div style="padding:6px 0 2px;text-align:right"><a href="https://waterdata.usgs.gov/nwis/uv?site_no={site_no}" target="_blank" style="color:#3498db;font-size:12px;text-decoration:none">View on USGS &#x2197;</a></div>' if site_no else ""}
            </div>
        </div>
    """


# -- Helpers --

def _resolve_states(state: str) -> list[str] | None:
    state = state.upper()
    if state == "ALL":
        return list(STATES.keys())
    if state in STATES:
        return [state]
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


async def _gauges_for_states(states_to_load: list[str]) -> list[dict]:
    today = datetime.now()
    today_key = (today.month, today.day)
    gauges: list[dict] = []

    for st in states_to_load:
        trout_gdf_for_state = await asyncio.to_thread(load_trout_streams, st)

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

        # Fetch historical stats in bulk for sites with discharge data
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

        for (site_name, latitude, longitude), info in sites.items():
            if not latitude or not longitude:
                continue

            variables = info["variables"]
            site_no = info.get("site_no")
            historical_median = _stats_cache.get(site_no, {}).get(today_key) if site_no else None

            conditions = score_conditions(variables, historical_median)
            overall = conditions["overall"]

            on_trout = False
            if trout_gdf_for_state is not None:
                on_trout = is_near_trout_stream(latitude, longitude, trout_gdf_for_state)

            popup_html = build_popup_html(
                site_name, variables, conditions, historical_median, on_trout, site_no
            )

            gauges.append({
                "site_no": site_no,
                "name": site_name,
                "lat": latitude,
                "lon": longitude,
                "conditions": conditions,
                "historical_median": historical_median,
                "on_trout": on_trout,
                "color": SCORE_COLORS[overall],
                "label": SCORE_LABELS[overall],
                "popup_html": popup_html,
            })

    return gauges


# -- Routes --

@app.head("/")
@app.get("/")
async def root():
    return RedirectResponse(url="/map?state=MD")


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

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(api_url, params=params)
        return response.json()


@app.get("/map")
async def map_shell():
    """Serves the static client shell; state/filters are resolved client-side."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/gauges")
async def api_gauges(state: str = Query(default="MD", description="MD, VA, WV, or 'all'.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}, or 'all'",
        )
    gauges = await _gauges_for_states(states_to_load)
    return {"state": state.upper(), "gauges": gauges}


@app.get("/api/waterways")
async def api_waterways(state: str = Query(default="MD", description="MD, VA, WV, or 'all'.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(status_code=400, detail=f"Unsupported state: {state}")
    gdfs = []
    for st in states_to_load:
        gdfs.append(await asyncio.to_thread(get_cached_shapefiles, st))
    return _gdf_to_geojson_response(gdfs)


@app.get("/api/trout")
async def api_trout(state: str = Query(default="MD", description="MD, VA, WV, or 'all'.")):
    states_to_load = _resolve_states(state)
    if states_to_load is None:
        raise HTTPException(status_code=400, detail=f"Unsupported state: {state}")
    gdfs = []
    for st in states_to_load:
        gdfs.append(await asyncio.to_thread(load_trout_streams, st))
    return _gdf_to_geojson_response(gdfs)


class PinIn(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    note: str = Field(default="", max_length=500)


@app.get("/api/pins")
async def api_list_pins():
    return await asyncio.to_thread(db.list_pins)


@app.post("/api/pins")
async def api_add_pin(pin: PinIn):
    return await asyncio.to_thread(db.add_pin, pin.lat, pin.lon, pin.note)


@app.delete("/api/pins/{pin_id}")
async def api_delete_pin(pin_id: int):
    deleted = await asyncio.to_thread(db.delete_pin, pin_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Pin not found")
    return {"ok": True}
