#!/usr/bin/env python3
"""Build data/stocking/CA.json from CDFW's two public stocking datasets.

California's stocking truth is split across two CDFW sources that have to be
joined -- neither alone has both "is it trout?" and "where is it?":

  A) FishPlants "Public Plant Search" (the owner-requested, TROUT-ONLY filter):
     https://nrm.dfg.ca.gov/FishPlants/PublicPlantSearch
     A server-rendered web app (no JSON API; the table is baked into the page,
     DataTables just paginates it client-side). Query string
     ?Params.PlantTimeFrame=1 = "All Plants" over the trailing ~year. Each row:
       Week of Plant | Water Name (+ a map link carrying stockid=<id>) |
       Counties | Species
     Species is coarse -- "Trout" or "Catfish" -- so it IS the trout filter
     (warmwater plants are mostly lakes; we keep Species == "Trout"). It gives
     names + the stockid join key but NO coordinates.

  B) "Recent Stocked Waters" [ds778] -- CDFW ArcGIS, has the GEOMETRY FishPlants
     lacks (polygons, last ~3 yrs):
     services2.arcgis.com/Uq9r85Potqm3MfRV/.../biosds778_fpu/FeatureServer/0
     Fields: StockingWaterID, DFGWATERID, Counties, last_yr_stkd. No species,
     no water name -- which is exactly why FishPlants is needed.

JOIN: FishPlants' stockid == ds778 StockingWaterID (the FishPlants map link is
apps.wildlife.ca.gov/fishing/?stockid=<StockingWaterID>). We key on
StockingWaterID, NOT DFGWATERID -- ~27% of ds778 rows have SWID != DFGWATERID,
and the FishPlants id is the StockingWaterID. We take each polygon's centroid
(returnCentroid, outSR=4326) for lat/lon.

We keep trout waters present in BOTH sources, drop anything outside CA's bbox,
and emit the stocking-overlay shape consumed by stocking.py:
{water, lat, lon, species[], category, season_months, agency_url}.

Like build_ut_stocking.py this is a COMMITTED SNAPSHOT, not a live feed (the
loader's live feeds are ArcGIS-only; FishPlants is a bespoke HTML app). Per the
"holdovers" rationale a recent snapshot is fine -- a water stocked last year
still fishes as trout water. Re-run to refresh.

[NETWORK -- run where both nrm.dfg.ca.gov and services2.arcgis.com are
reachable. The *.arcgis.com CDFW org is reachable from CI/build runs; the
nrm.dfg.ca.gov FishPlants host is on the egress allowlist.]

    python scripts/build_ca_stocking.py
"""
from __future__ import annotations

import html as htmllib
import json
import os
import re
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from states import STATE_BBOX  # noqa: E402

UA = {"User-Agent": "Blueliner/1.0 (+https://blueliner.app)"}
OUT = os.path.join(ROOT, "data", "stocking", "CA.json")

# FishPlants trailing-year "All Plants" table (server-rendered HTML).
FISHPLANTS = ("https://nrm.dfg.ca.gov/FishPlants/PublicPlantSearch"
              "?Params.PlantTimeFrame=1")
# Per-water CDFW map page (stockid == StockingWaterID) -- used as agency_url.
WATER_MAP = "https://apps.wildlife.ca.gov/fishing/?stockid={sid}"

# ds778 "Recent Stocked Waters" -- polygon geometry keyed by StockingWaterID.
DS778 = ("https://services2.arcgis.com/Uq9r85Potqm3MfRV/arcgis/rest/services/"
         "biosds778_fpu/FeatureServer/0/query")

# FishPlants only labels plants "Trout" vs "Catfish" (and other warmwater);
# the trout filter is therefore this single coarse class. CDFW doesn't publish
# the stocked species per plant in this list, so the overlay carries the
# generic "Trout" label (cf. UT, where UDWR exposed per-species detail).
TROUT_SPECIES = {"trout"}

_TAG = re.compile(r"<[^>]+>")


def _text(cell: str) -> str:
    return htmllib.unescape(_TAG.sub("", cell)).strip()


def fetch_fishplants(client: httpx.Client) -> dict[int, str]:
    """Return {stockid -> water name} for every trout plant in the trailing
    year. Dedupes a water's many weekly plants down to one entry."""
    r = client.get(FISHPLANTS)
    r.raise_for_status()
    page = r.text
    i = page.find('id="fishPlantsExternal"')
    if i < 0:
        raise RuntimeError("FishPlants results table not found in page")
    body = page[page.find("<tbody", i):page.find("</table>", i)]
    waters: dict[int, str] = {}
    seen_catfish = 0
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(tds) < 4:
            continue
        species = _text(tds[3]).lower()
        if species not in TROUT_SPECIES:
            seen_catfish += 1
            continue
        m = re.search(r"stockid=(\d+)", tds[1])
        if not m:
            continue
        sid = int(m.group(1))
        water = _text(re.sub(r"<a .*?</a>", "", tds[1], flags=re.S))
        if sid not in waters and water:
            waters[sid] = water
    print(f"FishPlants: {len(waters)} trout waters "
          f"({seen_catfish} non-trout plant rows skipped)")
    return waters


def fetch_ds778_centroids(client: httpx.Client) -> dict[int, tuple[float, float]]:
    """Return {StockingWaterID -> (lat, lon)} from ds778 polygon centroids."""
    r = client.get(DS778, params={
        "where": "1=1",
        "outFields": "StockingWaterID",
        "returnGeometry": "false",
        "returnCentroid": "true",
        "outSR": "4326",
        "f": "json",
    })
    r.raise_for_status()
    feats = r.json().get("features") or []
    cents: dict[int, tuple[float, float]] = {}
    no_centroid = 0
    for f in feats:
        c = f.get("centroid") or {}
        swid = (f.get("attributes") or {}).get("StockingWaterID")
        if swid is None or c.get("x") is None or c.get("y") is None:
            no_centroid += 1
            continue
        cents[int(swid)] = (float(c["y"]), float(c["x"]))
    print(f"ds778: {len(cents)} stocked-water centroids "
          f"({no_centroid} features without usable geometry)")
    return cents


def build() -> list[dict]:
    la0, la1, lo0, lo1 = STATE_BBOX["CA"]
    with httpx.Client(timeout=60.0, headers=UA, follow_redirects=True) as client:
        waters = fetch_fishplants(client)
        cents = fetch_ds778_centroids(client)

    rows: list[dict] = []
    dropped = {"no_geom": 0, "out_of_bbox": 0}
    for sid, water in waters.items():
        c = cents.get(sid)
        if not c:  # trout plant with no polygon in ds778 (often small ponds)
            dropped["no_geom"] += 1
            continue
        lat, lon = c
        if not (la0 <= lat <= la1 and lo0 <= lon <= lo1):
            dropped["out_of_bbox"] += 1
            continue
        rows.append({
            "water": water,
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "species": ["Trout"],
            "category": "Stocked trout water (CDFW)",
            "season_months": [1, 12],
            "agency_url": WATER_MAP.format(sid=sid),
        })

    rows.sort(key=lambda r: r["water"].lower())
    print(f"kept {len(rows)} trout waters; dropped {dropped}")
    return rows


def main() -> int:
    rows = build()
    if len(rows) < 50:
        print(f"ERROR: only {len(rows)} waters -- refusing to overwrite "
              f"(endpoint likely degraded)", file=sys.stderr)
        return 1
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
        f.write("\n")
    print(f"wrote {len(rows)} entries -> {os.path.relpath(OUT, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
