"""Ad-hoc ArcGIS recon probe [NETWORK -- runs in the Actions probe job].

Drill from an agency server down to a wirable layer + its field vocabulary,
without the trout-lexicon filter the discovery prober applies. The state sandbox
can't reach state ArcGIS hosts, so run this in the discovery workflow's
`probe_url` mode (open egress).

    python scripts/probe_layer.py <url>

Dispatches on the URL shape:
  * folder            (.../rest/services[/Folder])      -> list services
  * service root      (.../MapServer | .../FeatureServer) -> list layers/tables
  * layer             (.../MapServer/3)                  -> fields + distinct values
  * AGOL item/webmap  (...item.html?id= | .../items/ID)  -> operational layers
"""
from __future__ import annotations

import json
import re
import sys
import time

import httpx

UA = {"User-Agent": "Blueliner-discovery/0.1 (+https://blueliner.app)"}
TIMEOUT = 30.0
MAX_DISTINCT = 60


def _get(client: httpx.Client, url: str, params: dict) -> dict | None:
    for attempt in range(4):
        try:
            r = client.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                print(f"  [http {r.status_code}] {url}")
                return None
        except (httpx.TransportError, ValueError) as e:
            print(f"  [err {type(e).__name__}] {url}")
        time.sleep(min(2 ** attempt, 8))
    return None


def _dump_item(client: httpx.Client, item_id: str, _depth: int = 0) -> int:
    """AGOL item -> its service URL (feature-layer items) and/or operational
    layer titles + URLs (webmaps). Web Mapping Applications reference a webmap
    rather than carrying layers directly, so follow that one hop to reach the
    backing feature services."""
    base = f"https://www.arcgis.com/sharing/rest/content/items/{item_id}"
    root = _get(client, base, {"f": "json"})
    if root:
        print(f"item {item_id}: type={root.get('type')!r} title={root.get('title')!r}")
        if root.get("url"):
            print(f"  service url: {root['url']}")
    data = _get(client, base + "/data", {"f": "json"})
    oplayers = (data or {}).get("operationalLayers", [])
    if oplayers:
        print("  operational layers:")
        for lyr in oplayers:
            print(f"    {lyr.get('title')!r} -> {lyr.get('url')}")
    # Web Mapping Application / Dashboard -> hop to the referenced web map.
    if not oplayers and (data or {}) and _depth < 2:
        ref = ((data.get("map") or {}).get("itemId")
               or data.get("mapItemId") or data.get("webmap"))
        if ref and re.fullmatch(r"[0-9a-fA-F]{32}", str(ref)):
            print(f"  -> follows web map {ref}")
            return _dump_item(client, ref, _depth + 1)
    return 0 if (root or data) else 1


def _dump_service(client: httpx.Client, url: str, meta: dict) -> int:
    """Folder -> services; service root -> layers/tables."""
    if "services" in meta or "folders" in meta:
        for fld in meta.get("folders", []):
            print(f"  [folder] {fld}")
        for svc in meta.get("services", []):
            print(f"  [{svc.get('type')}] {svc.get('name')}")
        return 0
    for lyr in meta.get("layers", []) + meta.get("tables", []):
        print(f"  {lyr.get('id')}: {lyr.get('name')!r} ({lyr.get('geometryType', 'table')})")
    return 0


def _dump_layer(client: httpx.Client, layer: str, meta: dict) -> int:
    print(f"name : {meta.get('name')!r}   geometry: {meta.get('geometryType')}")
    print(f"supportedQueryFormats: {meta.get('supportedQueryFormats')!r}")
    cnt = _get(client, layer + "/query",
               {"where": "1=1", "returnCountOnly": "true", "f": "json"})
    print(f"count: {(cnt or {}).get('count')}")
    print("\nfields (name | type | alias | coded-domain):")
    string_fields = []
    for f in (meta.get("fields") or []):
        name, ftype = f.get("name"), f.get("type", "")
        coded = (f.get("domain") or {}).get("codedValues")
        coded_str = "; ".join(f"{c.get('code')}->{c.get('name')}" for c in coded) if coded else ""
        print(f"  {name} | {ftype.replace('esriFieldType','')} | {f.get('alias')!r} | {coded_str}")
        if ftype == "esriFieldTypeString":
            string_fields.append(name)
    print("\ndistinct values per string field:")
    for fname in string_fields:
        d = _get(client, layer + "/query", {
            "where": "1=1", "outFields": fname, "returnGeometry": "false",
            "returnDistinctValues": "true", "f": "json"})
        vals = sorted({a["attributes"].get(fname)
                       for a in (d or {}).get("features", [])
                       if a["attributes"].get(fname) not in (None, "")})
        print(f"\n  {fname}  ({len(vals)} distinct"
              f"{', truncated' if len(vals) > MAX_DISTINCT else ''}):")
        print(json.dumps(vals[:MAX_DISTINCT], indent=2))
    return 0


def main(url: str) -> int:
    # AGOL item / webmap?
    m = re.search(r"(?:[?&]id=|/items/)([0-9a-fA-F]{32})", url)
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        if m and "/rest/services" not in url:
            return _dump_item(client, m.group(1))
        base = url.split("?")[0].rstrip("/")
        print(f"target: {base}")
        meta = _get(client, base, {"f": "json"})
        if not meta:
            print("no metadata reached")
            return 1
        # Layer if it has a geometryType/fields; else folder/service root.
        if re.search(r"/\d+$", base) or "geometryType" in meta:
            return _dump_layer(client, base, meta)
        return _dump_service(client, base, meta)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/probe_layer.py <url>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
