"""Ad-hoc ArcGIS layer schema probe [NETWORK -- runs in the Actions probe job].

Dumps a feature layer's fields and the distinct values of each string field,
*without* the trout-lexicon filter the discovery prober applies. Use it to wire
a state whose dossier came back `whole_layer` because its category field (e.g.
Maine's habitat-value rating) doesn't speak trout-regulation vocabulary and so
was invisible to discovery.pick_category_field.

    python scripts/probe_layer.py <layer_url>

<layer_url> may point at a FeatureServer/MapServer (->/0 appended) or a specific
sublayer. The state sandbox can't reach state ArcGIS hosts, so this is meant to
run in the discovery/probe GitHub Actions job, which has egress.
"""
from __future__ import annotations

import json
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


def _layer_url(url: str) -> str:
    base = url.split("?")[0].rstrip("/")
    if base.endswith("FeatureServer") or base.endswith("MapServer"):
        return base + "/0"
    return base


def main(url: str) -> int:
    layer = _layer_url(url)
    with httpx.Client(timeout=TIMEOUT, headers=UA, follow_redirects=True) as client:
        meta = _get(client, layer, {"f": "json"})
        if not meta:
            print(f"no metadata reached for {layer}")
            return 1
        print(f"layer: {layer}")
        print(f"name : {meta.get('name')!r}   geometry: {meta.get('geometryType')}")
        cnt = _get(client, layer + "/query",
                   {"where": "1=1", "returnCountOnly": "true", "f": "json"})
        print(f"count: {(cnt or {}).get('count')}")
        print("\nfields (name | type | alias | coded-domain):")
        string_fields = []
        for f in (meta.get("fields") or []):
            name, ftype = f.get("name"), f.get("type", "")
            domain = f.get("domain") or {}
            coded = domain.get("codedValues")
            coded_str = ""
            if coded:
                coded_str = "; ".join(f"{c.get('code')}->{c.get('name')}" for c in coded)
            print(f"  {name} | {ftype.replace('esriFieldType','')} | "
                  f"{f.get('alias')!r} | {coded_str}")
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
            shown = vals[:MAX_DISTINCT]
            print(f"\n  {fname}  ({len(vals)} distinct"
                  f"{', truncated' if len(vals) > MAX_DISTINCT else ''}):")
            print(json.dumps(shown, indent=2))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/probe_layer.py <layer_url>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
