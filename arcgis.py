"""
ArcGIS REST helper.

State fisheries layers (trout streams, trout stocking) are served from ArcGIS
MapServer/FeatureServer endpoints. Those layers enforce a server-side
`maxRecordCount` (commonly 1000-2000); a single request silently truncates
larger layers. This fetches *all* features by paging with `resultOffset`,
which is why e.g. the Gunpowder Falls trout reach was previously missing.
"""

from urllib.parse import urlsplit, parse_qs

import geopandas
import httpx

USER_AGENT = "BlueLines/1.0 (+https://github.com/zmt28/BlueLines)"


def fetch_geojson_gdf(query_url: str, page_size: int = 1000):
    """
    Fetch every feature from an ArcGIS `.../query` URL, paginating until the
    server stops returning more. Returns a GeoDataFrame in EPSG:4326, or None
    on any failure (callers degrade gracefully).
    """
    split = urlsplit(query_url)
    base = f"{split.scheme}://{split.netloc}{split.path}"
    params = {k: v[0] for k, v in parse_qs(split.query).items()}
    params.update({"f": "geojson", "outSR": "4326", "returnGeometry": "true"})
    params.setdefault("where", "1=1")
    params.setdefault("outFields", "*")
    params["resultRecordCount"] = str(page_size)

    features: list[dict] = []
    offset = 0
    try:
        with httpx.Client(timeout=60.0, headers={"User-Agent": USER_AGENT}) as client:
            for _ in range(200):  # hard page cap (200k features) -- safety
                params["resultOffset"] = str(offset)
                resp = client.get(base, params=params)
                resp.raise_for_status()
                fc = resp.json()
                batch = fc.get("features", [])
                if not batch:
                    break
                features.extend(batch)
                exceeded = (
                    fc.get("properties", {}).get("exceededTransferLimit")
                    or fc.get("exceededTransferLimit")
                )
                # Done when the server signals no overflow and returned a
                # short page. (Layers without pagination support return the
                # same first page on every offset, so a short/!exceeded page
                # is the only safe stop -- the page cap bounds the worst case.)
                if len(batch) < page_size and not exceeded:
                    break
                offset += len(batch)
    except Exception:
        return None

    if not features:
        return None
    try:
        gdf = geopandas.GeoDataFrame.from_features(features, crs="EPSG:4326")
    except Exception:
        return None
    return gdf if not gdf.empty else None
