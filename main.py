from typing import Union
from fastapi import FastAPI
from fastapi.params import Body
import requests
import httpx
import json


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
