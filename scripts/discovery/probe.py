"""Layer probing + scoring [NETWORK -- runs in the Actions discovery job].

Given a candidate service/layer URL and the target state, fetch its `?f=json`
metadata and decide: is this a usable trout-water layer *for this state*, and
what's its category vocabulary?

Gates/signals, in order:
  1. geographic relevance -- the layer extent must intersect the state bbox
     (drops the out-of-state matches a fuzzy text search returns); repojected
     server-side via outSR=4326.
  2. geometry -- line/polygon preferred over point (points don't suit the NHD
     line join).
  3. anonymous queryability + non-empty.
  4. trout relevance -- the layer name/title or a field's distinct values speak
     trout; a layer with neither is almost certainly a wrong match.

`distinct_values` for the top category field is pulled via returnDistinctValues
so `classify` can draft the wild/stocked mapping. Mirrors the retry/keyset
patterns in build_clickable_streams.py.
"""
from __future__ import annotations

import time

import httpx

from . import geo, lexicon

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 30.0
GEOMETRY_RANK = {"esriGeometryPolyline": 3, "esriGeometryPolygon": 2,
                 "esriGeometryPoint": 0}
_LEX = lexicon.WILD_TOKENS + lexicon.STOCKED_TOKENS + lexicon.AMBIGUOUS_TOKENS


def _get(client: httpx.Client, url: str, params: dict) -> dict | None:
    for attempt in range(4):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                return None
        except (httpx.TransportError, ValueError):
            pass
        time.sleep(min(2 ** attempt, 8))
    return None


def _layer_url(url: str) -> str:
    base = url.split("?")[0].rstrip("/")
    if base.endswith("FeatureServer") or base.endswith("MapServer"):
        return base + "/0"
    return base


def _extent_wgs84(client: httpx.Client, layer: str, meta: dict) -> dict | None:
    """Prefer a server-reprojected extent (outSR=4326, any source CRS); fall back
    to converting the layer-metadata extent."""
    d = _get(client, layer + "/query",
             {"where": "1=1", "returnExtentOnly": "true", "outSR": "4326", "f": "json"})
    ext = (d or {}).get("extent")
    if ext and "xmin" in ext:
        ext.setdefault("spatialReference", {"wkid": 4326})
        return geo.to_wgs84(ext)
    return geo.to_wgs84(meta.get("extent"))


def _looks_like_category(values) -> bool:
    blob = " ".join(str(v).lower() for v in values)
    return any(t in blob for t in _LEX)


def probe(candidate: dict, state: str) -> dict | None:
    """Probe one candidate for `state` -> a scored record, or None if
    unreachable / out-of-state / unusable."""
    layer = _layer_url(candidate["url"])
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        meta = _get(client, layer, {"f": "json"})
        if not meta or "geometryType" not in meta:
            return None

        # (1) geographic relevance -- drop out-of-state matches.
        ext = _extent_wgs84(client, layer, meta)
        if not geo.extent_intersects(ext, state):
            return None

        geom = meta.get("geometryType", "")
        caps = (meta.get("capabilities") or "").lower()
        fields = [f["name"] for f in meta.get("fields", [])
                  if f.get("type") == "esriFieldTypeString"]

        category_field, distinct = None, []
        for fname in fields[:12]:
            d = _get(client, layer + "/query", {
                "where": "1=1", "outFields": fname, "returnGeometry": "false",
                "returnDistinctValues": "true", "f": "json"})
            vals = [a["attributes"].get(fname) for a in (d or {}).get("features", [])]
            vals = [v for v in vals if v]
            if vals and len(vals) <= 40 and _looks_like_category(vals):
                category_field, distinct = fname, sorted(set(vals))
                break

        count = _get(client, layer + "/query",
                     {"where": "1=1", "returnCountOnly": "true", "f": "json"})
        n = (count or {}).get("count", 0)

        name_blob = f"{layer} {candidate.get('title', '')}".lower()
        trout = "trout" in name_blob
        fishy = trout or "fish" in name_blob or "stream" in name_blob

        score = GEOMETRY_RANK.get(geom, 0)
        score += 2 if "query" in caps else -5      # must be anonymously queryable
        score += 1 if n else -3
        score += 2 if category_field else 0
        score += 2 if trout else 0
        # (4) trout relevance: no trout/fish/stream name AND no category field
        # -> almost certainly a wrong match; sink it below usable candidates.
        score += -4 if (not fishy and not category_field) else 0
        return {
            "url": layer, "title": candidate.get("title", ""),
            "source": candidate.get("source", "?"), "geometry": geom,
            "feature_count": n, "anonymous_query": "query" in caps,
            "in_state": geo.extent_intersects(ext, state) and ext is not None,
            "category_field": category_field, "distinct_values": distinct,
            "trout_named": trout, "score": score,
        }
