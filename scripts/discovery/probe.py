"""Layer probing + scoring [NETWORK -- runs in the Actions discovery job].

Given a candidate service/layer URL, fetch its `?f=json` metadata and decide:
is this a usable trout-water layer, and what's its category vocabulary? We
prefer line/polygon over point geometry (points don't suit the NHD line join),
require anonymous queryability and a non-empty layer, and reward a field whose
distinct values hit the regulation lexicon. The numeric `score` ranks a state's
candidates so the dossier leads with the best one.

`distinct_values` for the top category field is pulled via
returnDistinctValues so `classify` can draft the wild/stocked mapping. Mirrors
the retry/keyset patterns already in build_clickable_streams.py.
"""
from __future__ import annotations

import time

import httpx

from . import lexicon

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 30.0
GEOMETRY_RANK = {"esriGeometryPolyline": 3, "esriGeometryPolygon": 2,
                 "esriGeometryPoint": 0}


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
    """Resolve a service-or-layer URL to a concrete layer URL (default /0)."""
    base = url.split("?")[0].rstrip("/")
    if base.endswith("FeatureServer") or base.endswith("MapServer"):
        return base + "/0"
    return base


def _looks_like_category(field_name: str, values) -> bool:
    """A field is the classification field if its distinct values speak trout
    regulation (any lexicon token fires on any value)."""
    toks = lexicon.WILD_TOKENS + lexicon.STOCKED_TOKENS + lexicon.AMBIGUOUS_TOKENS
    blob = " ".join(str(v).lower() for v in values)
    return any(t in blob for t in toks)


def probe(candidate: dict) -> dict | None:
    """Probe one candidate -> a scored record, or None if unreachable/unusable."""
    layer = _layer_url(candidate["url"])
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        meta = _get(client, layer, {"f": "json"})
        if not meta or "geometryType" not in meta:
            return None
        geom = meta.get("geometryType", "")
        caps = (meta.get("capabilities") or "").lower()
        fields = [f["name"] for f in meta.get("fields", [])
                  if f.get("type") == "esriFieldTypeString"]

        # Probe string fields for a regulation vocabulary (cheap: distinct only).
        category_field, distinct = None, []
        for fname in fields[:12]:
            d = _get(client, layer + "/query", {
                "where": "1=1", "outFields": fname, "returnGeometry": "false",
                "returnDistinctValues": "true", "f": "json"})
            vals = [a["attributes"].get(fname) for a in (d or {}).get("features", [])]
            vals = [v for v in vals if v]
            if vals and len(vals) <= 40 and _looks_like_category(fname, vals):
                category_field, distinct = fname, sorted(set(vals))
                break

        count = _get(client, layer + "/query",
                     {"where": "1=1", "returnCountOnly": "true", "f": "json"})
        n = (count or {}).get("count", 0)

        score = GEOMETRY_RANK.get(geom, 0)
        score += 2 if "query" in caps else -5      # must be anonymously queryable
        score += 1 if n else -3
        score += 2 if category_field else 0
        return {
            "url": layer, "title": candidate.get("title", ""),
            "source": candidate.get("source", "?"), "geometry": geom,
            "feature_count": n, "anonymous_query": "query" in caps,
            "category_field": category_field, "distinct_values": distinct,
            "score": score,
        }
