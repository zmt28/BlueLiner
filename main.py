from typing import Union
from fastapi import FastAPI
from fastapi.params import Body
from fastapi.responses import FileResponse
import requests
import httpx
import json
import folium
from datetime import datetime
from collections import defaultdict
import geopandas
import branca


app = FastAPI()


@app.get("/get_MDstreams")
async def get_MDstreams():
    api_url = 'https://waterservices.usgs.gov/nwis/iv/'
    format = 'json'
    stateCd = 'md'
    siteStatus = 'active'
    siteType = ['ST', 'FA-WWTP', 'SP', 'ST-TS']
    
    params = {
        'format': format,
        'stateCd': stateCd,
        'siteStatus': siteStatus,
        'siteType': ','.join(siteType)
    }      

    async with httpx.AsyncClient() as client:
        response = await client.get(api_url, params=params)
        data = response.json()
        print(json.dumps(data, indent=4))
        return response.json()
    

linear_streams = ["https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24001_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24003_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24005_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24009_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24011_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24013_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24015_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24017_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24019_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24021_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24023_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24025_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24027_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24029_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24031_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24033_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24035_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24037_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24039_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24041_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24043_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24045_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24047_linearwater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/LINEARWATER/tl_2024_24510_linearwater.zip"
                     ]

area_water = ["https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24001_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24003_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24005_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24009_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24011_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24013_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24015_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24017_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24019_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24021_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24023_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24025_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24027_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24029_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24031_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24033_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24035_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24037_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24039_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24041_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24043_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24045_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24047_areawater.zip",
                     "https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_24510_areawater.zip"
                     ]


@app.get("/create_map")
async def create_map():
    data = await get_MDstreams()

    time_series = data.get("value", {}).get("timeSeries",[])

    m = folium.Map(
        location=[44.967243, -103.771556], 
        tiles="OpenStreetMap", 
        zoom_start=4,
        max_bounds=True,
    )

    for file in linear_streams:
        county_streams = geopandas.read_file(file)
        linear_streams_tooltip = folium.GeoJsonTooltip(
            fields=["FULLNAME"],
            aliases=["Name:"],
            localize=True,
            labels=True,
        )
        folium.GeoJson(county_streams,tooltip=linear_streams_tooltip,).add_to(m)

    for file in area_water:
        county_area_water = geopandas.read_file(file)
        country_water_tooltip = folium.GeoJsonTooltip(
            fields=["FULLNAME"],
            aliases=["Name:"],
            localize=True,
            labels=True,
        )
        folium.GeoJson(county_area_water, tooltip=country_water_tooltip).add_to(m)

    sites = defaultdict(list)
    for series in time_series:
        source_info = series.get("sourceInfo", {})
        site_name = source_info.get('siteName').capitalize()
        geo_location = source_info.get("geoLocation", {}).get("geogLocation", {})
        latitude = geo_location.get('latitude')
        longitude = geo_location.get('longitude')
        variable_description = series.get("variable", {}).get("variableDescription")
        values_list = series.get("values", [])
        if values_list:
            value_data = values_list[0].get("value", [])
            if value_data:
                value_entry = value_data[0]
                value = value_entry.get("value")
                date_Time = value_entry.get("dateTime")
                sites[(site_name, latitude, longitude)].append({
                    "variable": variable_description,
                    "value": value,
                    "dateTime": date_Time
                })

    for (site_name, latitude, longitude), variables in sites.items():
        if latitude and longitude:
            rows = ""
            for variable in variables:
                variable_description = variable["variable"]
                variable_value = variable["value"]
                variable_dateTime = datetime.fromisoformat(variable["dateTime"])
                formatted_variable_dateTime = variable_dateTime.strftime("%B %d, %Y %I:%M %p")
                rows += f"""
                     <tr>
                        <td style="border:1px solid black">{variable_description}</td>
                        <td style="border:1px solid black;text-align: center;padding: 8px">{variable_value}</td>
                        <td style="border:1px solid black;text-align: center">{formatted_variable_dateTime}</td>
                    </tr>
                """

        html = f"""
            <html>
            <h1>{site_name}</h1><br>
            <table style="border-collapse: collapse;width:100%">
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
            icon=folium.Icon(color="blue"),
        ).add_to(m)

    map_file_path = "map.html"
    m.save(map_file_path)

    return FileResponse(map_file_path)
