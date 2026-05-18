"""
ArcGIS REST helper.

State fisheries layers (trout streams, trout stocking) are served from
ArcGIS MapServer/FeatureServer endpoints with a server-side `maxRecordCount`
(commonly 1000-2000). We page with `resultOffset` to get the whole layer --
but many MapServer layers DON'T support pagination and silently ignore
`resultOffset`, returning the same first page forever. So this is strictly
bounded: a wall-clock budget, a low page cap, short per-request timeouts,
and explicit non-pagination detection. Any failure/timeout returns None and
callers degrade gracefully (trout/stocking just don't tag).
"""

import time
from urllib.parse import urlsplit, parse_qs

import geopandas
import httpx

USER_AGENT = "BlueLines/1.0 (+https://github.com/zmt28/BlueLines)"

_REQUEST_TIMEOUT = 15.0   # per HTTP request
_TOTAL_BUDGET = 20.0      # whole pagination loop, wall-clock
_MAX_PAGES = 15


def fetch_geojson_gdf(query_url: str, page_size: int = 1000):
    """
    Fetch features from an ArcGIS `.../query` URL, paging with `resultOffset`
    until the server is exhausted, it stops making progress, or a strict
    time/page budget is hit. Returns a GeoDataFrame (EPSG:4326) or None.
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
    first_page_sig = None
    deadline = time.monotonic() + _TOTAL_BUDGET
    try:
        with httpx.Client(
            timeout=_REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            for _ in range(_MAX_PAGES):
                if time.monotonic() > deadline:
                    break
                params["resultOffset"] = str(offset)
                resp = client.get(base, params=params)
                resp.raise_for_status()
                fc = resp.json()
                batch = fc.get("features", [])
                if not batch:
                    break

                # Non-pagination detection: if a later page's first feature
                # is identical to the first page's, the server ignored
                # resultOffset -- stop (keep what we already have).
                sig = repr(batch[0])
                if first_page_sig is None:
                    first_page_sig = sig
                elif sig == first_page_sig:
                    break

                features.extend(batch)
                exceeded = (
                    fc.get("properties", {}).get("exceededTransferLimit")
                    or fc.get("exceededTransferLimit")
                )
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
