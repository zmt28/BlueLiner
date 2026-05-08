from contextlib import asynccontextmanager
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx
import folium
from datetime import datetime
from collections import defaultdict
import geopandas
import branca
import asyncio

from states import STATES, US_CENTER, get_linear_urls, get_area_urls
from trout import load_trout_streams, is_near_trout_stream


# Module-level caches populated at startup
_shapefile_cache: dict[str, list[geopandas.GeoDataFrame]] = {}
_stats_cache: dict[str, dict] = {}


def _load_shapefiles_for_state(state_code: str) -> list[geopandas.GeoDataFrame]:
    """Load and cache all TIGER shapefiles for a state. Called once at startup."""
    gdfs = []
    for url in get_linear_urls(state_code) + get_area_urls(state_code):
        try:
            gdfs.append(geopandas.read_file(url))
        except Exception:
            continue
    return gdfs


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load Maryland shapefiles at startup so the first request is fast.
    # Other states load on first request.
    _shapefile_cache["MD"] = await asyncio.to_thread(_load_shapefiles_for_state, "MD")
    yield


app = FastAPI(lifespan=lifespan)


def get_cached_shapefiles(state_code: str) -> list[geopandas.GeoDataFrame]:
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
                     historical_median: float | None, on_trout: bool = False) -> str:
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
            </div>
        </div>
    """


# -- Application shell HTML --

def build_app_html(state: str) -> str:
    state_options = ""
    for code, info in STATES.items():
        selected = "selected" if code == state else ""
        state_options += f'<option value="{code}" {selected}>{info["name"]}</option>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BlueLines -- Stream Conditions</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        .header {{
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: white;
            padding: 14px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            z-index: 1000;
            position: relative;
        }}
        .header-left {{ display: flex; align-items: center; gap: 14px; }}
        .logo {{
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.5px;
            color: #e2e8f0;
        }}
        .logo span {{ color: #4fc3f7; }}
        .tagline {{
            font-size: 13px;
            color: #94a3b8;
            letter-spacing: 0.3px;
        }}
        .header-right {{ display: flex; align-items: center; gap: 12px; }}
        .state-select {{
            background: rgba(255,255,255,0.1);
            color: white;
            border: 1px solid rgba(255,255,255,0.2);
            border-radius: 6px;
            padding: 6px 10px;
            font-size: 14px;
            cursor: pointer;
        }}
        .state-select option {{ background: #1a1a2e; color: white; }}
        .map-container {{
            position: absolute;
            top: 56px;
            bottom: 0;
            left: 0;
            right: 0;
        }}
        .map-container iframe {{
            width: 100%;
            height: 100%;
            border: none;
        }}
        .legend {{
            position: fixed;
            bottom: 24px;
            left: 24px;
            background: rgba(255,255,255,0.95);
            backdrop-filter: blur(8px);
            border-radius: 10px;
            padding: 14px 18px;
            box-shadow: 0 2px 12px rgba(0,0,0,0.15);
            z-index: 1001;
            font-size: 13px;
        }}
        .legend-title {{
            font-weight: 700;
            color: #1a1a2e;
            margin-bottom: 8px;
            font-size: 13px;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
            color: #444;
        }}
        .legend-dot {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <div>
                <div class="logo">Blue<span>Lines</span></div>
                <div class="tagline">Real-time stream conditions for fly fishermen</div>
            </div>
        </div>
        <div class="header-right">
            <select class="state-select" onchange="window.location.href='/map?state='+this.value">
                {state_options}
                <option value="all" {"selected" if state == "ALL" else ""}>All States</option>
            </select>
        </div>
    </div>
    <div class="map-container">
        <iframe src="/map/raw?state={state}"></iframe>
    </div>
    <div class="legend">
        <div class="legend-title">Fishing Conditions</div>
        <div class="legend-item"><div class="legend-dot" style="background:#2ecc71"></div> Good</div>
        <div class="legend-item"><div class="legend-dot" style="background:#f39c12"></div> Fair</div>
        <div class="legend-item"><div class="legend-dot" style="background:#e74c3c"></div> Poor</div>
        <div class="legend-item"><div class="legend-dot" style="background:#95a5a6"></div> No Data</div>
        <div style="border-top:1px solid #e0e0e0;margin:8px 0 6px"></div>
        <div class="legend-item"><div class="legend-dot" style="background:#1abc9c"></div> Trout Stream</div>
    </div>
</body>
</html>"""


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
async def map_shell(state: str = Query(default="MD", description="Two-letter state code (MD, VA, WV). Use 'all' for all states.")):
    state = state.upper()
    if state != "ALL" and state not in STATES:
        return {"error": f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}, or 'all'"}
    return HTMLResponse(content=build_app_html(state))


@app.get("/map/raw")
async def create_map(state: str = Query(default="MD", description="Two-letter state code (MD, VA, WV). Use 'all' for all states.")):
    """
    Generates and returns the raw Folium map HTML with styled popups,
    color-coded markers, and layer controls.
    """
    state = state.upper()
    if state == "ALL":
        states_to_load = list(STATES.keys())
    elif state in STATES:
        states_to_load = [state]
    else:
        return {"error": f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}, or 'all'"}

    if len(states_to_load) == 1:
        center = STATES[states_to_load[0]]["center"]
        zoom = 8
    else:
        center = US_CENTER
        zoom = 6

    m = folium.Map(
        location=center,
        tiles="CartoDB positron",
        zoom_start=zoom,
        max_bounds=True,
    )

    trout_gdfs: dict[str, geopandas.GeoDataFrame] = {}
    waterways_group = folium.FeatureGroup(name="Waterways")
    trout_streams_group = folium.FeatureGroup(name="Trout Streams")

    for st in states_to_load:
        gdfs = await asyncio.to_thread(get_cached_shapefiles, st)
        for gdf in gdfs:
            try:
                tooltip = folium.GeoJsonTooltip(
                    fields=["FULLNAME"], aliases=[""], localize=True, labels=False,
                    style="font-size:12px;font-weight:600;color:#1a1a2e;",
                )
                folium.GeoJson(
                    gdf,
                    tooltip=tooltip,
                    style_function=lambda x: {
                        "color": "#4a90d9",
                        "weight": 1.2,
                        "opacity": 0.4,
                    },
                ).add_to(waterways_group)
            except Exception:
                continue

        trout_gdf = await asyncio.to_thread(load_trout_streams, st)
        if trout_gdf is not None and not trout_gdf.empty:
            trout_gdfs[st] = trout_gdf
            name_field = "NAME" if "NAME" in trout_gdf.columns else (
                "GNIS_Name" if "GNIS_Name" in trout_gdf.columns else
                "STream_Nam" if "STream_Nam" in trout_gdf.columns else None
            )
            tooltip_kwargs = (
                {"fields": [name_field], "aliases": [""], "labels": False,
                 "style": "font-size:12px;font-weight:600;color:#0e6655;"}
                if name_field else {}
            )
            folium.GeoJson(
                trout_gdf,
                tooltip=folium.GeoJsonTooltip(**tooltip_kwargs) if tooltip_kwargs else None,
                style_function=lambda x: {
                    "color": "#1abc9c",
                    "weight": 2.5,
                    "opacity": 0.7,
                },
            ).add_to(trout_streams_group)

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
        today = datetime.now()
        today_key = (today.month, today.day)

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

        # Create feature groups for the trout toggle
        trout_group = folium.FeatureGroup(name="Trout Stream Gauges")
        other_group = folium.FeatureGroup(name="All Other Gauges")
        trout_gdf_for_state = trout_gdfs.get(st)

        for (site_name, latitude, longitude), info in sites.items():
            if not latitude or not longitude:
                continue

            variables = info["variables"]
            site_no = info.get("site_no")
            historical_median = _stats_cache.get(site_no, {}).get(today_key) if site_no else None

            conditions = score_conditions(variables, historical_median)
            score = conditions["overall"]
            color = SCORE_COLORS[score]

            on_trout = False
            if trout_gdf_for_state is not None:
                on_trout = is_near_trout_stream(latitude, longitude, trout_gdf_for_state)

            trout_badge = ""
            if on_trout:
                trout_badge = ' <span style="color:#1abc9c;font-size:11px">&#x1f41f; Trout Water</span>'

            popup_html = build_popup_html(site_name, variables, conditions, historical_median, on_trout)
            iframe = branca.element.IFrame(html=popup_html, width=420, height=340)
            popup = folium.Popup(iframe, max_width=420)

            marker = folium.CircleMarker(
                location=[latitude, longitude],
                radius=7,
                color=color,
                weight=2,
                fill=True,
                fill_color=color,
                fill_opacity=0.8,
                tooltip=f"<b>{site_name}</b>{trout_badge}<br><span style='color:{color}'>{SCORE_LABELS[score]}</span>",
                popup=popup,
            )

            if on_trout:
                marker.add_to(trout_group)
            else:
                marker.add_to(other_group)

        trout_group.add_to(m)
        other_group.add_to(m)

    waterways_group.add_to(m)
    trout_streams_group.add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    map_html = m._repr_html_()
    return HTMLResponse(content=map_html)


@app.get("/get_MDstreams")
async def get_MDstreams():
    """Legacy endpoint."""
    return await get_streams(state="MD")


@app.get("/create_map")
async def create_map_legacy():
    """Legacy endpoint."""
    return await create_map()
