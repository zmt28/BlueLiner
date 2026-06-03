"""Candidate-endpoint generation [NETWORK -- runs in the Actions discovery job].

Turns a state code into a ranked list of candidate ArcGIS layer URLs by querying
public catalogs, so discovery stops depending on a human typing search terms:

  * ArcGIS Online / Hub search  (services.arcgis.com items, org-agnostic)
  * data.gov CKAN               (federal aggregator; many state DNR datasets)
  * ArcGIS server directory walk (seeded state GIS hosts -> recurse ?f=json)

Candidates are deduped by normalized service URL and returned best-first; the
prober (`probe.py`) does the real vetting (geometry, anonymous query, fields).
This sandbox's egress blocks these hosts, so nothing here runs locally -- it's
exercised by .github/workflows/trout-discovery-spike.yml.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import httpx

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 30.0

# Search phrases, most-specific first; {st} is the full state name.
QUERY_TEMPLATES = (
    "{st} wild trout streams",
    "{st} stocked trout streams",
    "{st} trout streams regulations",
    "{st} designated trout waters",
    "{st} trout fishing",
)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin",
    "WY": "Wyoming",
}

# Optional seed hosts for the directory-walk adapter (extend as discovered).
SEED_ARCGIS_HOSTS = {
    "WI": ["https://dnrmaps.wi.gov/arcgis/rest/services"],
    "MI": ["https://gisp.mcgi.state.mi.us/arcgis/rest/services"],
}


def _get(client: httpx.Client, url: str, params: dict) -> dict | None:
    for attempt in range(4):
        try:
            r = client.get(url, params=params)
            if r.status_code < 500:
                return r.json() if r.status_code == 200 else None
        except (httpx.TransportError, ValueError):
            pass
        time.sleep(min(2 ** attempt, 8))
    return None


def _norm_service(url: str) -> str:
    """Collapse a layer URL to its service root for dedupe (drop /<layer> and
    /query)."""
    u = url.split("?")[0].rstrip("/")
    for marker in ("/FeatureServer", "/MapServer"):
        if marker in u:
            return u[: u.index(marker) + len(marker)]
    return u


def _from_arcgis_search(client: httpx.Client, terms: str) -> list[dict]:
    """AGOL search -> Feature/Map service items carrying a `url`."""
    data = _get(client, "https://www.arcgis.com/sharing/rest/search", {
        "q": terms, "f": "json", "num": 20,
        "filter": '(type:"Feature Service" OR type:"Map Service")',
    })
    out = []
    for item in (data or {}).get("results", []):
        url = item.get("url")
        if url:
            out.append({"url": url, "title": item.get("title", ""),
                        "source": "arcgis-search"})
    return out


def _from_ckan(client: httpx.Client, terms: str) -> list[dict]:
    """data.gov CKAN -> resources that look like ArcGIS/Esri services."""
    data = _get(client, "https://catalog.data.gov/api/3/action/package_search",
                {"q": terms, "rows": 10})
    out = []
    for pkg in (data or {}).get("result", {}).get("results", []):
        for res in pkg.get("resources", []):
            url, fmt = res.get("url", ""), (res.get("format") or "").lower()
            if "rest/services" in url and ("FeatureServer" in url or "MapServer" in url):
                out.append({"url": url, "title": pkg.get("title", ""),
                            "source": "data.gov"})
            elif fmt in ("geojson", "esri rest"):
                out.append({"url": url, "title": pkg.get("title", ""),
                            "source": "data.gov"})
    return out


def find_candidates(state: str, top_k: int = 8) -> list[dict]:
    """Best-first, deduped candidate layer/service URLs for one state."""
    name = STATE_NAMES.get(state.upper(), state)
    seen: set[str] = set()
    ranked: list[dict] = []
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        for tmpl in QUERY_TEMPLATES:
            terms = tmpl.format(st=name)
            for cand in _from_arcgis_search(client, terms) + _from_ckan(client, terms):
                key = _norm_service(cand["url"])
                if key in seen:
                    continue
                seen.add(key)
                ranked.append(cand)
                if len(ranked) >= top_k:
                    return ranked
    return ranked
