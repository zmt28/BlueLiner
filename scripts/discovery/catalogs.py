"""Candidate-endpoint generation [NETWORK -- runs in the Actions discovery job].

Turns a state code into a ranked list of candidate ArcGIS layer URLs from three
adapters, so discovery stops depending on a human typing search terms:

  * directory walk   -- recurse seeded state-GIS ArcGIS-Server roots (?f=json),
                        keep services whose name says trout/fish. This is the
                        ONLY adapter that finds the many state-hosted layers
                        (VA/MD/NY/WV/PA/...) that AGOL search never indexes.
  * ArcGIS Online/Hub -- services.arcgis.com items (covers AGOL-published states
                        like NC/GA and agency Hubs).
  * data.gov CKAN     -- federal aggregator; catches some state DNR datasets.

Walk results lead (state-authoritative, high precision); search/CKAN backfill.
Deduped by service URL. The geo gate in probe.py drops any out-of-state match a
fuzzy text search slips through.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 30.0

QUERY_TEMPLATES = (
    "{st} wild trout streams",
    "{st} stocked trout streams",
    "{st} trout streams regulations",
    "{st} designated trout waters",
    "{st} DNR trout fishing",
    "{st} trout stream classification",
    "{st} fish stocking locations",
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

# Seeded state-GIS ArcGIS-Server `rest/services` roots for the directory walk.
# These are the public fisheries/DNR map servers; a production crawl would
# maintain one or two per state. Unreachable roots are skipped silently.
SEED_ARCGIS_HOSTS = {
    "VA": ["https://services.dwr.virginia.gov/arcgis/rest/services"],
    "MD": ["https://dnr.geodata.md.gov/dnrdata/rest/services"],
    "NJ": ["https://mapsdep.nj.gov/arcgis/rest/services"],
    "VT": ["https://anrmaps.vermont.gov/arcgis/rest/services"],
    "NY": ["https://gisservices.dec.ny.gov/arcgis/rest/services"],
    "WV": ["https://services.wvgis.wvu.edu/arcgis/rest/services"],
    "PA": ["https://mapservices.pasda.psu.edu/server/rest/services"],
    "MA": ["https://arcgisserver.digital.mass.gov/arcgisserver/rest/services"],
    "WI": ["https://dnrmaps.wi.gov/arcgis/rest/services"],
    "MI": ["https://gisp.mcgi.state.mi.us/arcgis/rest/services"],
    # CO is AGOL-hosted now; walk the CPW org so the walk reaches Aquatic
    # Sportfish Management Waters / cutthroat conservation layers instead of the
    # CPWAdminData points AGOL search grabbed. Self-hosted root kept as fallback.
    "CO": ["https://services5.arcgis.com/ttNGmDvKQA7oeDQ3/arcgis/rest/services",
           "https://gis.cpw.state.co.us/arcgis/rest/services"],
    "TN": ["https://tnmap.tn.gov/arcgis/rest/services"],
    # MN Geospatial Commons DNR folder. The mndnr/rest root 500s, and AGOL
    # search only surfaced the single undifferentiated trout-designation layer
    # (no wild/stocked split); walking the DNR folder enumerates every MN DNR
    # fisheries layer so a management/survey-class layer can surface instead.
    "MN": ["https://enterprise.gisdata.mn.gov/aghost/rest/services/us_mn_state_dnr"],
    "MT": ["https://fwp-gis.mt.gov/arcgis/rest/services"],
    # IDFG hosting portal exposes a Fisheries folder the bare server root lacks.
    "ID": ["https://gisportal-idfg.idaho.gov/hosting/rest/services",
           "https://gis.idfg.idaho.gov/server/rest/services"],
    "UT": ["https://maps.dnr.utah.gov/arcgis/rest/services"],
    # WY + NH are AGOL-hosted (no self-hosted root). WGFD publishes a real
    # "Trout Stream Classifications" line layer; NHFG publishes fish-stocking.
    # NB: WY's classes are biomass ribbons (Blue/Red/Yellow/Green), UT's are
    # Blue Ribbon quality designations -- neither is a wild/stocked split, so
    # those two likely surface as flag/skip dossiers, not clean onboards.
    "WY": ["https://services6.arcgis.com/cWzdqIyxbijuhPLw/arcgis/rest/services"],
    "NH": ["https://services8.arcgis.com/hg1B9Egwk1I5p300/arcgis/rest/services"],
}

_NAME_HINTS = ("trout", "fish", "coldwater", "angler")


def _relevance(cand: dict) -> int:
    """Rank key: trout-named first, then fish/other -- so the specific trout
    service isn't out-ranked off the top-K by generic fisheries layers (the
    Phase-2 MD/VA recall misses)."""
    blob = f"{cand['url']} {cand.get('title', '')}".lower()
    return 0 if "trout" in blob else (1 if "fish" in blob else 2)


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
    u = url.split("?")[0].rstrip("/")
    for marker in ("/FeatureServer", "/MapServer"):
        if marker in u:
            return u[: u.index(marker) + len(marker)]
    return u


def _walk(client: httpx.Client, root: str, depth: int = 2) -> list[dict]:
    """Recurse an ArcGIS-Server directory, keeping services named trout/fish."""
    data = _get(client, root, {"f": "json"})
    if not data:
        return []
    out = []
    for svc in data.get("services", []):
        name, typ = svc.get("name", ""), svc.get("type", "")
        if typ in ("MapServer", "FeatureServer") and \
                any(h in name.lower() for h in _NAME_HINTS):
            out.append({"url": f"{root.rsplit('/services', 1)[0]}/services/{name}/{typ}",
                        "title": name.split("/")[-1], "source": "dir-walk"})
    if depth > 0:
        for folder in data.get("folders", []):
            out += _walk(client, f"{root}/{folder}", depth - 1)
    return out


def _from_directory_walk(client: httpx.Client, state: str) -> list[dict]:
    out = []
    for root in SEED_ARCGIS_HOSTS.get(state.upper(), []):
        out += _walk(client, root)
    return out


def _from_arcgis_search(client: httpx.Client, terms: str) -> list[dict]:
    data = _get(client, "https://www.arcgis.com/sharing/rest/search", {
        "q": terms, "f": "json", "num": 20,
        "filter": '(type:"Feature Service" OR type:"Map Service")',
    })
    out = []
    for item in (data or {}).get("results", []):
        if item.get("url"):
            out.append({"url": item["url"], "title": item.get("title", ""),
                        "source": "arcgis-search"})
    return out


def _from_ckan(client: httpx.Client, terms: str) -> list[dict]:
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
    """Best-first, deduped candidate layer/service URLs for one state.

    Directory-walk hits lead (state-authoritative); AGOL search + CKAN backfill.
    """
    name = STATE_NAMES.get(state.upper(), state)
    seen: set[str] = set()
    ranked: list[dict] = []

    def _add(cands):
        for cand in cands:
            key = _norm_service(cand["url"])
            if key in seen:
                continue
            seen.add(key)
            ranked.append(cand)

    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        _add(_from_directory_walk(client, state))
        for tmpl in QUERY_TEMPLATES:
            terms = tmpl.format(st=name)
            _add(_from_arcgis_search(client, terms))
            _add(_from_ckan(client, terms))
            if len(ranked) >= top_k * 3:
                break
    # Stable sort by trout-relevance so the right layer leads the top-K.
    ranked.sort(key=_relevance)
    return ranked[:top_k]
