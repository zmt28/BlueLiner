from typing import Optional
from fastapi import FastAPI, Query
from fastapi.responses import FileResponse
import httpx
import json
import folium
from datetime import datetime
from collections import defaultdict
import geopandas
import branca

from states import STATES, US_CENTER, get_linear_urls, get_area_urls


app = FastAPI()


def score_conditions(variables: list[dict]) -> dict:
    """
    Evaluates a monitoring station's current sensor readings and produces a
    fishing conditions assessment. Scoring is optimized for cold-water species
    (trout). Returns a dict with per-metric scores and an overall rating.

    Temperature thresholds (Fahrenheit):
        Green: 48-65, Yellow: 45-48 or 65-68, Red: above 68 or below 40

    Flow is scored when discharge data is available -- extreme values
    (negative or very high) are flagged, but without historical percentiles
    the scoring is conservative.
    """
    temp_score = None
    flow_score = None

    for var in variables:
        description = var.get("variable", "").lower()
        try:
            value = float(var.get("value", ""))
        except (ValueError, TypeError):
            continue

        # USGS parameter 00010: water temperature, always reported in Celsius
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

        # USGS parameter 00060: discharge in cubic feet per second
        if "discharge" in description or "streamflow" in description:
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
    }


def condition_label(score: str) -> str:
    labels = {
        "green": "Good",
        "yellow": "Fair",
        "red": "Poor",
        "gray": "Insufficient Data",
    }
    return labels.get(score, "Unknown")


def marker_color(score: str) -> str:
    colors = {
        "green": "green",
        "yellow": "orange",
        "red": "red",
        "gray": "gray",
    }
    return colors.get(score, "blue")


@app.get("/streams")
async def get_streams(state: str = Query(default="MD", description="Two-letter state code (MD, VA, WV)")):
    """
    Fetches real-time stream and water monitoring data from the USGS National
    Water Information System (NWIS) instantaneous values API.

    Queries all active sites in the specified state of types: streams (ST),
    springs (SP), stream-type sites (ST-TS), and wastewater treatment plants
    (FA-WWTP).

    Returns the raw USGS JSON response containing time series data for each
    active monitoring site.
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
async def create_map(state: Optional[str] = Query(default=None, description="Two-letter state code (MD, VA, WV). Omit for all states.")):
    """
    Generates and returns an interactive HTML map with waterway geometries
    and live sensor markers for the specified state (or all supported states).

    For each state, this endpoint:
    1. Fetches live sensor data from USGS NWIS
    2. Loads Census TIGER/Line shapefiles for waterway rendering
    3. Scores each station's conditions for fishing suitability
    4. Renders color-coded markers on a Folium map

    Returns an HTML file containing the interactive map.
    """
    if state:
        state = state.upper()
        if state not in STATES:
            return {"error": f"Unsupported state: {state}. Supported: {', '.join(STATES.keys())}"}
        states_to_load = [state]
    else:
        states_to_load = list(STATES.keys())

    # Set map center based on whether we're showing one state or all
    if len(states_to_load) == 1:
        center = STATES[states_to_load[0]]["center"]
        zoom = 8
    else:
        center = US_CENTER
        zoom = 6

    m = folium.Map(
        location=center,
        tiles="OpenStreetMap",
        zoom_start=zoom,
        max_bounds=True,
    )

    for st in states_to_load:
        # Census TIGER/Line shapefiles define the physical geometry of waterways.
        # Linear water features are streams and rivers, area water features are
        # lakes and ponds. Loading these per-county gives the map its river lines.
        for file in get_linear_urls(st):
            try:
                county_streams = geopandas.read_file(file)
                tooltip = folium.GeoJsonTooltip(
                    fields=["FULLNAME"],
                    aliases=["Name:"],
                    localize=True,
                    labels=True,
                )
                folium.GeoJson(county_streams, tooltip=tooltip).add_to(m)
            except Exception:
                continue

        for file in get_area_urls(st):
            try:
                county_area = geopandas.read_file(file)
                tooltip = folium.GeoJsonTooltip(
                    fields=["FULLNAME"],
                    aliases=["Name:"],
                    localize=True,
                    labels=True,
                )
                folium.GeoJson(county_area, tooltip=tooltip).add_to(m)
            except Exception:
                continue

        # Fetch live USGS sensor data and aggregate multiple readings per site
        # into a single marker. Each site can report several variables (temperature,
        # discharge, gage height, etc.) across its time series.
        data = await get_streams(state=st)
        time_series = data.get("value", {}).get("timeSeries", [])

        sites = defaultdict(list)
        for series in time_series:
            source_info = series.get("sourceInfo", {})
            site_name = source_info.get("siteName", "Unknown").capitalize()
            geo_location = source_info.get("geoLocation", {}).get("geogLocation", {})
            latitude = geo_location.get("latitude")
            longitude = geo_location.get("longitude")
            variable_description = series.get("variable", {}).get("variableDescription")
            values_list = series.get("values", [])
            if values_list:
                value_data = values_list[0].get("value", [])
                if value_data:
                    value_entry = value_data[0]
                    sites[(site_name, latitude, longitude)].append({
                        "variable": variable_description,
                        "value": value_entry.get("value"),
                        "dateTime": value_entry.get("dateTime"),
                    })

        for (site_name, latitude, longitude), variables in sites.items():
            if not latitude or not longitude:
                continue

            # Score this station's conditions based on temperature and flow
            conditions = score_conditions(variables)
            score = conditions["overall"]
            label = condition_label(score)

            condition_html = f"""
                <div style="padding:6px 0;font-size:14px;font-weight:bold;color:{'#2d7d2d' if score == 'green' else '#b8860b' if score == 'yellow' else '#cc3333' if score == 'red' else '#888'}">
                    Fishing Conditions: {label}
                </div>
            """

            rows = ""
            for variable in variables:
                variable_description = variable["variable"]
                variable_value = variable["value"]
                variable_dateTime = datetime.fromisoformat(variable["dateTime"])
                formatted_variable_dateTime = variable_dateTime.strftime("%B %d, %Y %I:%M %p")
                rows += f"""
                    <tr>
                        <td style="border:1px solid black">{variable_description}</td>
                        <td style="border:1px solid black;text-align:center;padding:8px">{variable_value}</td>
                        <td style="border:1px solid black;text-align:center">{formatted_variable_dateTime}</td>
                    </tr>
                """

            html = f"""
                <html>
                <h1>{site_name}</h1>
                {condition_html}
                <table style="border-collapse:collapse;width:100%">
                    <tr>
                        <th style="border:1px solid black">Variable</th>
                        <th style="border:1px solid black">Value</th>
                        <th style="border:1px solid black">dateTime</th>
                    </tr>
                    {rows}
                </table>
                </html>
            """

            iframe = branca.element.IFrame(html=html, width=500, height=300)
            popup = folium.Popup(iframe, max_width=500)

            folium.Marker(
                location=[latitude, longitude],
                tooltip=site_name,
                popup=popup,
                icon=folium.Icon(color=marker_color(score)),
            ).add_to(m)

    map_file_path = "map.html"
    m.save(map_file_path)

    return FileResponse(map_file_path)


# Keep the original endpoint as an alias for backwards compatibility
@app.get("/get_MDstreams")
async def get_MDstreams():
    """Legacy endpoint. Redirects to /streams?state=MD."""
    return await get_streams(state="MD")


@app.get("/create_map")
async def create_map_legacy():
    """Legacy endpoint. Redirects to /map."""
    return await create_map()
