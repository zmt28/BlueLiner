"""
ArcGIS REST helper.

State fisheries layers (trout streams, trout stocking) are ArcGIS
MapServer/FeatureServer endpoints with a server-side `maxRecordCount`.
Many MapServer layers do NOT support `resultOffset` paging (they silently
ignore it and return the same first page forever), so we page by
**OBJECTID keyset** instead -- `where <oid> > <last>` ordered ascending --
which works regardless of pagination support and gives full coverage.

Strictly bounded: a wall-clock budget, a page cap, short per-request
timeouts. Any failure/timeout returns None and callers degrade
gracefully (trout/stocking just don't tag). This runs cached + in the
background, never on the hot request path.
"""

import time
from urllib.parse import urlsplit, parse_qs

import geopandas
import httpx

USER_AGENT = "Blueliner/1.0 (+https://blueliner.app)"

_REQUEST_TIMEOUT = 15.0   # per HTTP request
_TOTAL_BUDGET = 90.0      # whole pagination loop (cached + backgrounded)
_MAX_PAGES = 60
_OID_FALLBACKS = ("OBJECTID", "OBJECTID_12", "FID", "ESRI_OID", "objectid")


def _discover_oid_field(client, layer_url: str) -> str | None:
    try:
        r = client.get(layer_url, params={"f": "json"})
        r.raise_for_status()
        return r.json().get("objectIdField") or None
    except Exception:
        return None


def _oid_of(feature: dict, oid_field: str):
    props = feature.get("properties") or {}
    val = props.get(oid_field)
    if val is None:
        val = feature.get("id")
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def fetch_geojson_gdf(query_url: str, page_size: int = 1000):
    """
    Fetch every feature from an ArcGIS `.../query` URL via OBJECTID keyset
    paging. Returns a GeoDataFrame (EPSG:4326) or None.
    """
    split = urlsplit(query_url)
    base = f"{split.scheme}://{split.netloc}{split.path}"
    layer_url = f"{split.scheme}://{split.netloc}{split.path.rsplit('/query', 1)[0]}"
    src = {k: v[0] for k, v in parse_qs(split.query).items()}
    user_where = src.get("where", "1=1")
    common = {
        "f": "geojson",
        "outSR": "4326",
        "returnGeometry": "true",
        "outFields": src.get("outFields", "*"),
        "resultRecordCount": str(page_size),
    }

    features: list[dict] = []
    deadline = time.monotonic() + _TOTAL_BUDGET
    try:
        with httpx.Client(
            timeout=_REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            oid = _discover_oid_field(client, layer_url)
            for cand in (oid, *_OID_FALLBACKS):
                if cand:
                    oid = cand
                    break

            last: int | None = None
            for _ in range(_MAX_PAGES):
                if time.monotonic() > deadline:
                    break
                params = dict(common)
                if oid:
                    bound = -1 if last is None else last
                    params["where"] = f"({user_where}) AND {oid} > {bound}"
                    params["orderByFields"] = f"{oid} ASC"
                else:
                    params["where"] = user_where  # no key -> single page

                resp = client.get(base, params=params)
                resp.raise_for_status()
                batch = resp.json().get("features", [])
                if not batch:
                    break

                if not oid:
                    features.extend(batch)
                    break
                ids = [i for i in (_oid_of(f, oid) for f in batch) if i is not None]
                if not ids:
                    features.extend(batch)
                    break
                mx = max(ids)
                if last is not None and mx <= last:
                    break  # server ignored the keyset -> stop (no dup append)
                features.extend(batch)
                last = mx
                if len(batch) < page_size:
                    break
    except Exception:
        return None

    if not features:
        return None
    try:
        gdf = geopandas.GeoDataFrame.from_features(features, crs="EPSG:4326")
    except Exception:
        return None
    return gdf if not gdf.empty else None
