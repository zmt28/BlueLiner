import folium
import json
import httpx
import asyncio
import branca
from datetime import datetime
from collections import defaultdict

async def fetch_MDstreams_data():
    api_url = 'http://127.0.0.1:8000/get_MDstreams'
    async with httpx.AsyncClient() as client:
        response = await client.get(api_url)
        response.raise_for_status()
        return response.json()

async def create_map():
    data = await fetch_MDstreams_data()

    time_series = data.get("value", {}).get("timeSeries",[])
    print(time_series)

    m = folium.Map(
        location=[44.967243, -103.771556], 
        tiles="OpenStreetMap", 
        zoom_start=4,
        max_bounds=True,
    )
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

    m.save("MDstreams_map.html")
    print("Map saved as MDstreams_map.html")

asyncio.run(create_map())