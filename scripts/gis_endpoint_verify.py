"""One-shot GIS endpoint discovery + verification for western states.

[NETWORK -- runs in the Actions gis-endpoint-verify job; the dev sandbox's
egress allowlist blocks state ArcGIS hosts.]

Two modes:
  * default        -- per state: enumerate known agency AGOL org catalogs +
                      self-hosted ArcGIS Server roots + ArcGIS Online search,
                      then auto-verify keyword-matching layers (count, f=geojson
                      sample, in-state bbox check) and print a structured log.
  * --urls "ST|url;ST|url" -- verify ONLY these explicit layer URLs, with the
                      full field dump (types + 3 sample features).

Verification protocol per layer (all must pass):
  1. <layer>?f=json                       -> name, geometryType, fields
  2. <layer>/query returnCountOnly        -> record count
  3. <layer>/query f=geojson n=3 4326     -> valid GeoJSON features
  4. sample coordinates inside state bbox
"""
from __future__ import annotations

import argparse
import json
import time

import httpx

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 15.0

BBOX = {  # (W, S, E, N) slightly padded
    "MT": (-116.2, 44.3, -103.9, 49.1),
    "ID": (-117.3, 41.9, -110.9, 49.1),
    "WY": (-111.2, 40.9, -103.9, 45.1),
    "CO": (-109.2, 36.9, -101.9, 41.1),
    "UT": (-114.2, 36.9, -108.9, 42.1),
    "NM": (-109.2, 31.2, -102.9, 37.1),
    "AZ": (-115.0, 31.2, -108.9, 37.1),
    "NV": (-120.1, 34.9, -113.9, 42.1),
    "CA": (-124.6, 32.4, -113.9, 42.1),
    "OR": (-124.8, 41.8, -116.3, 46.4),
    "WA": (-124.9, 45.4, -116.8, 49.1),
}

STATE_NAMES = {
    "MT": "Montana", "ID": "Idaho", "WY": "Wyoming", "CO": "Colorado",
    "UT": "Utah", "NM": "New Mexico", "AZ": "Arizona", "NV": "Nevada",
    "CA": "California", "OR": "Oregon", "WA": "Washington",
}

ORG_CATALOGS = {
    "WY": ["https://services6.arcgis.com/cWzdqIyxbijuhPLw/arcgis/rest/services"],
    "UT": ["https://services.arcgis.com/ZzrwjTRez6FJiOq4/arcgis/rest/services"],
    "CO": ["https://services5.arcgis.com/ttNGmDvKQA7oeDQ3/arcgis/rest/services"],
    "CA": ["https://services2.arcgis.com/Uq9r85Potqm3MfRV/arcgis/rest/services"],
    "NV": ["https://services.arcgis.com/RyxlXSfFi87rAosq/arcgis/rest/services"],
}

SERVER_ROOTS = {
    "MT": ["https://fwp-gis.mt.gov/arcgis/rest/services",
           "https://gisservicemt.gov/arcgis/rest/services"],
    "ID": ["https://gisportal-idfg.idaho.gov/hosting/rest/services",
           "https://gis.idfg.idaho.gov/server/rest/services"],
    "CO": ["https://ndismaps.nrel.colostate.edu/arcgis/rest/services"],
    "UT": ["https://maps.dnr.utah.gov/arcgis/rest/services"],
    "WA": ["https://geodataservices.wdfw.wa.gov/arcgis/rest/services"],
    "CA": ["https://map.dfg.ca.gov/arcgis/rest/services"],
}

BASE_TERMS = (
    "{n} trout stocking",
    "{n} fish stocking locations",
    "{n} fishing access sites",
    "{n} boating access boat ramp",
)
EXTRA_TERMS = {
    "CA": ["CDFW fishing guide stocked", "California fish planting"],
    "OR": ["ODFW trout stocking schedule", "ODFW fishing access boat"],
    "WA": ["WDFW water access sites", "WDFW trout stocking plants"],
    "AZ": ["AZGFD fish stocking", "Arizona community fishing AZGFD"],
    "NM": ["New Mexico Game and Fish fishing", "NMDGF stocking waters"],
    "MT": ["Montana FWP fishing access site", "Montana FWP stocking"],
    "ID": ["Idaho Fish and Game fishing access", "IDFG fish stocking"],
    "NV": ["NDOW fishable waters", "Nevada NDOW boat ramps"],
    "WY": ["Wyoming Game Fish stocked", "WGFD public access fishing"],
    "UT": ["Utah DWR stocked waters", "Utah community fisheries"],
    "CO": ["CPW fishing access boating", "Colorado stocked waters CPW"],
}

STOCK_KW = ("stock", "plant", "hatch")
ACCESS_KW = ("access", "ramp", "fas", "boat", "pfa", "angl", "launch")
FISH_KW = ("fish", "trout")
NOISE_KW = ("huc", "county", "boundar", "parcel", "geolog", "fire", "elk",
            "deer", "hunt", "bird", "sheep", "moose", "bear", "lion", "wolf",
            "antelope", "turkey", "grouse", "raptor", "bat_", "frog", "toad")
SOFT_NOISE = ("passage", "barrier", "wac", "shellfish", "crab", "clam",
              "urchin", "cucumber", "aquaculture", "landmark", "pheasant",
              "mussel", "kayak", "survey", "ais_risk", "infrastructure",
              "screen", "culvert", "habitat project", "smelt", "label")

AGENCY_HINTS = (
    "fwp-gis.mt.gov", "gisservicemt.gov", "idfg.idaho.gov",
    "ndismaps.nrel.colostate.edu", "maps.dnr.utah.gov",
    "geodataservices.wdfw.wa.gov", "map.dfg.ca.gov", "data.wsdot.wa.gov",
    "cWzdqIyxbijuhPLw", "ZzrwjTRez6FJiOq4", "ttNGmDvKQA7oeDQ3",
    "Uq9r85Potqm3MfRV", "RyxlXSfFi87rAosq",
)

MAX_VERIFY_PER_STATE = 16


def rank(blob: str, url: str) -> int:
    b = blob.lower()
    s = 0
    if any(k in b for k in STOCK_KW):
        s += 4
    if any(k in b for k in ACCESS_KW):
        s += 4
    if "trout" in b:
        s += 2
    if "fish" in b:
        s += 1
    if any(h in url for h in AGENCY_HINTS):
        s += 3
    if any(k in b for k in SOFT_NOISE):
        s -= 5
    return s


def get(c: httpx.Client, url: str, params: dict | None = None):
    for attempt in range(3):
        try:
            r = c.get(url, params=params or {})
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    return None
            if r.status_code < 500:
                return {"_http": r.status_code}
        except httpx.TransportError:
            pass
        time.sleep(attempt + 1)
    return None


def interesting(name: str) -> bool:
    n = name.lower()
    if any(k in n for k in NOISE_KW):
        return False
    return any(k in n for k in STOCK_KW + ACCESS_KW + FISH_KW)


def classify(blob: str) -> str:
    b = blob.lower()
    if any(k in b for k in STOCK_KW):
        return "stocking?"
    if any(k in b for k in ACCESS_KW):
        return "access?"
    return "fish?"


def first_coord(geom):
    if not geom:
        return None
    c = geom.get("coordinates")
    if c is None:
        return None
    while isinstance(c, (list, tuple)) and c and isinstance(c[0], (list, tuple)):
        c = c[0]
    return c if isinstance(c, (list, tuple)) and len(c) >= 2 else None


def in_bbox(st: str, lon: float, lat: float) -> bool:
    w, s, e, n = BBOX[st]
    return w <= lon <= e and s <= lat <= n


def trim_props(props: dict, limit_keys: int = 18, limit_val: int = 48) -> dict:
    out = {}
    for k, v in list(props.items())[:limit_keys]:
        s = str(v)
        out[k] = s[:limit_val] + ("..." if len(s) > limit_val else "")
    return out


def verify_layer(c: httpx.Client, st: str, url: str, full: bool = False) -> bool:
    meta = get(c, url, {"f": "json"})
    if not meta or "_http" in meta or "error" in meta:
        print(f"  LAYER {url}\n    FAIL meta: {meta}")
        return False
    name, geom = meta.get("name"), meta.get("geometryType")
    fields = meta.get("fields") or []
    cnt = get(c, url + "/query",
              {"where": "1=1", "returnCountOnly": "true", "f": "json"})
    count = (cnt or {}).get("count")
    gj = get(c, url + "/query", {
        "where": "1=1", "outFields": "*", "f": "geojson",
        "resultRecordCount": "3", "outSR": "4326"})
    ok = bool(isinstance(gj, dict) and gj.get("type") == "FeatureCollection"
              and gj.get("features"))
    if not ok and fields:
        # joined views sometimes choke on outFields=* under f=geojson;
        # retry with explicit string fields
        strs = [f["name"] for f in fields
                if f.get("type") == "esriFieldTypeString"]
        sub = strs[0] if strs else ""
        gj = get(c, url + "/query", {
            "where": "1=1", "outFields": sub or "", "f": "geojson",
            "resultRecordCount": "3", "outSR": "4326"})
        ok = bool(isinstance(gj, dict) and gj.get("type") == "FeatureCollection"
                  and gj.get("features"))
        if ok:
            print("    [note: f=geojson required explicit outFields, "
                  "outFields=* fails on this layer]")
    instate = None
    samples = []
    if ok:
        feats = gj["features"]
        coords = [xy for xy in (first_coord(f.get("geometry")) for f in feats) if xy]
        instate = all(in_bbox(st, xy[0], xy[1]) for xy in coords) if coords else None
        n_samp = 3 if full else 2
        for f in feats[:n_samp]:
            p = f.get("properties") or {}
            samples.append(p if full else trim_props(p))
    verdict = "PASS" if (ok and count and instate is not False) else "FAIL"
    print(f"  LAYER {url}")
    print(f"    {verdict} name={name!r} geom={geom} count={count} "
          f"geojson_ok={ok} in_state={instate}")
    if full:
        print("    fields:")
        for f in fields:
            coded = (f.get("domain") or {}).get("codedValues")
            cs = (" coded[" + "; ".join(
                f"{cv.get('code')}->{cv.get('name')}" for cv in coded[:12]) + "]"
                ) if coded else ""
            print(f"      {f.get('name')} | "
                  f"{str(f.get('type', '')).replace('esriFieldType', '')} | "
                  f"{f.get('alias')!r}{cs}")
    else:
        print(f"    fields={[f.get('name') for f in fields][:30]}")
    for s in samples:
        print(f"    sample={json.dumps(s, default=str)[:1400]}")
    return verdict == "PASS"


def service_layers(c: httpx.Client, svc_url: str) -> list[tuple[str, str, str]]:
    """-> [(layer_url, layer_name, geometryType)]"""
    meta = get(c, svc_url, {"f": "json"})
    if not meta or "_http" in meta:
        return []
    out = []
    for lyr in (meta.get("layers") or []):
        if lyr.get("subLayerIds"):  # group layer
            continue
        out.append((f"{svc_url}/{lyr.get('id')}", lyr.get("name") or "",
                    lyr.get("geometryType") or lyr.get("type") or ""))
    return out


def walk_root(c: httpx.Client, root: str, depth: int = 1) -> list[tuple[str, str]]:
    data = get(c, root, {"f": "json"})
    if not data or "_http" in data:
        print(f"  [root unreachable] {root} -> {data}")
        return []
    out = []
    for svc in data.get("services", []):
        name, typ = svc.get("name", ""), svc.get("type", "")
        if typ in ("MapServer", "FeatureServer"):
            url = f"{root.rsplit('/services', 1)[0]}/services/{name}/{typ}"
            out.append((url, name))
    if depth > 0:
        for folder in data.get("folders", []):
            out += walk_root(c, f"{root}/{folder}", depth - 1)
    return out


def agol_search(c: httpx.Client, term: str) -> list[dict]:
    data = get(c, "https://www.arcgis.com/sharing/rest/search", {
        "q": term, "f": "json", "num": "15",
        "filter": '(type:"Feature Service" OR type:"Map Service")'})
    return (data or {}).get("results", []) or []


def discover_state(c: httpx.Client, st: str) -> None:
    print(f"\n======== {st} ========")
    layer_cands: list[tuple[str, str, str]] = []  # (layer_url, blob, src)
    seen_svc: set[str] = set()

    def add_service(svc_url: str, blob: str, src: str):
        u = svc_url.rstrip("/")
        if u in seen_svc:
            return
        seen_svc.add(u)
        print(f"  CAND[{src}] {classify(blob)} {u}   ({blob[:90]})")
        for lurl, lname, lgeom in service_layers(c, u):
            if interesting(lname) or interesting(blob):
                if not any(k in lname.lower() for k in NOISE_KW):
                    layer_cands.append((lurl, f"{blob} :: {lname}", src))

    # 1) agency org catalogs + self-hosted roots
    for root in ORG_CATALOGS.get(st, []) + SERVER_ROOTS.get(st, []):
        for url, name in walk_root(c, root):
            if interesting(name):
                add_service(url, name, "catalog")

    # 2) ArcGIS Online search
    terms = [t.format(n=STATE_NAMES[st]) for t in BASE_TERMS] + EXTRA_TERMS.get(st, [])
    for term in terms:
        for item in agol_search(c, term):
            url = (item.get("url") or "").rstrip("/")
            if not url or "/rest/services" not in url:
                continue
            blob = f"{item.get('title', '')} owner={item.get('owner', '')}"
            print(f"  AGOL[{term[:28]}] {blob[:110]}\n      -> {url}")
            # direct layer URL?
            if url.rsplit("/", 1)[-1].isdigit():
                layer_cands.append((url, blob, "agol"))
            else:
                add_service(url, blob, "agol")

    # 3) rank, dedupe (prefer FeatureServer twin over MapServer), verify top-K
    seen_lyr: set[str] = set()
    ranked = []
    for lurl, blob, src in layer_cands:
        key = lurl.replace("/MapServer/", "/FeatureServer/")
        if key in seen_lyr:
            continue
        seen_lyr.add(key)
        ranked.append((rank(blob, lurl), lurl, blob, src))
    ranked.sort(key=lambda t: -t[0])
    for i, (sc, lurl, blob, src) in enumerate(ranked):
        if i >= MAX_VERIFY_PER_STATE or sc <= 0:
            print(f"  [skipped rank={sc} {lurl} ({blob[:70]})]")
            continue
        print(f"  -- verify rank={sc} [{src}] guess={classify(blob)} ({blob[:90]})")
        try:
            verify_layer(c, st, lurl)
        except Exception as e:  # keep the batch alive
            print(f"    EXC {type(e).__name__}: {e}")


def parse_request(path: str) -> tuple[str, str, list, list, list]:
    """Lines:
        states: MT,ID,...
        ST|layerUrl            -- explicit layer, full dump
        svc: ST|serviceUrl     -- enumerate + quick-verify EVERY layer
        org: ST|restServicesRoot -- walk catalog, verify keyword layers
        search: ST|term        -- AGOL search, list items, verify their layers
    """
    states, urls, svcs, orgs, searches = "", [], [], [], []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or line == "urls:":
                    continue
                low = line.lower()
                if low.startswith("states:"):
                    states = line.split(":", 1)[1].strip()
                elif low.startswith("svc:"):
                    svcs.append(line.split(":", 1)[1].strip())
                elif low.startswith("org:"):
                    orgs.append(line.split(":", 1)[1].strip())
                elif low.startswith("search:"):
                    searches.append(line.split(":", 1)[1].strip())
                elif "|" in line:
                    urls.append(line)
    except OSError:
        pass
    return states, ";".join(urls), svcs, orgs, searches


def run_svc(c: httpx.Client, st: str, svc_url: str) -> None:
    print(f"\n==== SVC {st} {svc_url} ====")
    for lurl, lname, lgeom in service_layers(c, svc_url):
        print(f"  layer {lurl}  {lname!r} ({lgeom})")
        try:
            verify_layer(c, st, lurl)
        except Exception as e:
            print(f"    EXC {type(e).__name__}: {e}")


def run_org(c: httpx.Client, st: str, root: str, cap: int = 12) -> None:
    print(f"\n==== ORG {st} {root} ====")
    n = 0
    for url, name in walk_root(c, root):
        if not interesting(name):
            continue
        print(f"  CAND {classify(name)} {url}  ({name[:90]})")
        for lurl, lname, lgeom in service_layers(c, url):
            if not (interesting(lname) or interesting(name)):
                continue
            if any(k in lname.lower() for k in NOISE_KW):
                continue
            if rank(f"{name} :: {lname}", lurl) <= 0:
                continue
            if n >= cap:
                continue
            n += 1
            print(f"  -- {lname!r}")
            try:
                verify_layer(c, st, lurl)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")


def run_search(c: httpx.Client, st: str, term: str, cap: int = 10) -> None:
    print(f"\n==== SEARCH {st} {term!r} ====")
    n = 0
    for item in agol_search(c, term):
        url = (item.get("url") or "").rstrip("/")
        print(f"  ITEM {item.get('title', '')!r} owner={item.get('owner', '')} -> {url}")
        if not url or "/rest/services" not in url:
            continue
        layers = ([(url, "", "")] if url.rsplit("/", 1)[-1].isdigit()
                  else service_layers(c, url))
        for lurl, lname, lgeom in layers:
            if n >= cap:
                break
            n += 1
            print(f"  -- {lname!r}")
            try:
                verify_layer(c, st, lurl)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--states", default="")
    ap.add_argument("--urls", default="")
    ap.add_argument("--request", default="")
    args = ap.parse_args()
    svcs, orgs, searches = [], [], []
    if args.request and not (args.states or args.urls):
        args.states, args.urls, svcs, orgs, searches = parse_request(args.request)
    if not any([args.states, args.urls, svcs, orgs, searches]):
        args.states = ",".join(STATE_NAMES)

    def split_pair(pair):
        st, _, rest = pair.partition("|")
        return st.strip().upper(), rest.strip()

    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as c:
        for pair in args.urls.split(";"):
            if not pair.strip():
                continue
            st, url = split_pair(pair)
            print(f"\n======== {st} explicit ========")
            try:
                verify_layer(c, st, url, full=True)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")
        for pair in svcs:
            st, url = split_pair(pair)
            try:
                run_svc(c, st, url)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")
        for pair in orgs:
            st, url = split_pair(pair)
            try:
                run_org(c, st, url)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")
        for pair in searches:
            st, term = split_pair(pair)
            try:
                run_search(c, st, term)
            except Exception as e:
                print(f"    EXC {type(e).__name__}: {e}")
        if any([args.urls.strip(), svcs, orgs, searches]):
            return 0
        for st in [s.strip().upper() for s in args.states.split(",") if s.strip()]:
            try:
                discover_state(c, st)
            except Exception as e:
                print(f"  STATE-EXC {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
