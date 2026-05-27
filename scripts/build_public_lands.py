#!/usr/bin/env python3
"""
Build data/public_lands/public_lands.geojson.gz -- the vector overlay
of every parcel in PAD-US 4.0 (USGS GAP's Protected Areas Database),
tinted by manager type on the map and tagged with the per-unit public
access tier (Open / Restricted / Closed / Unknown).

Each output feature carries:
    unit_name, manager_type, manager_name, designation,
    public_access, state_nm

Geometry is the Polygon / MultiPolygon, simplified at SIMPLIFY_TOL
(same value used for clickable_streams; ~33 m at mid-latitudes,
beyond what matters for "which parcel did the user click").

Dev dependencies (NOT in runtime requirements.txt):
    pip install httpx geopandas pyogrio shapely

Run from repo root:
    python scripts/build_public_lands.py

The output is hosted on Cloudflare R2 alongside vaa.csv.gz +
clickable_streams.geojson.gz -- see CONTRIBUTING.md "Refreshing
PAD-US" for the upload + redeploy playbook.

PAD-US download URLs change between releases. The SLICES list below
points at the current 4.0 manager-type GeoJSON exports from USGS GAP.
On a future release (4.1, 5.0, ...) the operator updates the URLs +
expected vintage suffix once, re-runs this script, and bumps the
versioned R2 prefix (`/v1/` -> `/v2/`) in the Render env var.
"""

import gzip
import json
import os
import sys
import tempfile

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "public_lands")
OUT_PATH = os.path.join(OUT_DIR, "public_lands.geojson.gz")

# Same simplification tolerance as build_clickable_streams.py -- enough
# to drop redundant vertices without altering which parcel a click
# falls in. preserve_topology=False is fine: parcels don't share
# critical edges with each other for our purposes.
SIMPLIFY_TOL = 0.0003

# PAD-US 4.0 GeoJSON-by-Manager-Type slices. Federal is by far the
# largest; private (easements only -- ordinary private land isn't
# mapped) is small but worth including so the "private easement"
# legend swatch isn't always empty. Each slice is downloaded, parsed,
# simplified, and streamed into the merged output.
#
# These URLs reflect the 4.0 release as published by USGS GAP on
# ScienceBase. They go stale on each new release; cross-check at
# https://www.usgs.gov/programs/gap-analysis-project/science/pad-us-data-download
# before re-running and update the `url` fields to point at the
# current vintage.
SLICES = [
    {"manager": "Federal",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_fed.geojson"},
    {"manager": "State",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_state.geojson"},
    {"manager": "Tribal",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_tribal.geojson"},
    {"manager": "Local",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_local.geojson"},
    {"manager": "NGO",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_ngo.geojson"},
    {"manager": "Private",
     "url": "https://www.sciencebase.gov/catalog/file/get/"
            "65aab0a8d34ef8c8c7d3eb1c?f=__disk__pa_dus_private.geojson"},
]

# PAD-US source field -> canonical client-facing field. We aggressively
# prune; the runtime table only needs these six attrs + geometry. Any
# field not listed here is dropped at write time (saves ~30-40% of the
# output payload).
FIELD_MAP = {
    "Unit_Nm":     "unit_name",
    "Mang_Type":   "manager_type",
    "Mang_Name":   "manager_name",
    "Des_Tp":      "designation",
    "Pub_Access":  "public_access",
    "State_Nm":    "state_nm",
}


def download(url: str, dest: str) -> None:
    sys.stdout.write(f"  fetch {os.path.basename(dest)} ... ")
    sys.stdout.flush()
    with httpx.stream("GET", url, timeout=600.0,
                      follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"{os.path.getsize(dest) / 1e6:.0f} MB")


def simplify_and_clean(features, manager_default: str):
    """Yield canonical-shape features one at a time so the caller can
    stream-write to disk without buffering the whole national set."""
    # Lazy import so the script's `--help` works on a clean machine.
    from shapely.geometry import mapping, shape
    for f in features:
        props_in = f.get("properties") or {}
        props_out = {client: props_in.get(src)
                     for src, client in FIELD_MAP.items()}
        # Per-slice manager type is authoritative when the source
        # field is blank -- guarantees the client always gets a
        # color-coded fill.
        if not props_out.get("manager_type"):
            props_out["manager_type"] = manager_default
        geom = f.get("geometry")
        if not geom:
            continue
        try:
            g = shape(geom).buffer(0)         # fix self-intersections
            if g.is_empty:
                continue
            g = g.simplify(SIMPLIFY_TOL, preserve_topology=False)
            if g.is_empty:
                continue
        except Exception:
            continue
        yield {
            "type": "Feature",
            "properties": props_out,
            "geometry": mapping(g),
        }


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    total = 0
    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[')
        first = True
        with tempfile.TemporaryDirectory() as tmp:
            for slice_ in SLICES:
                mgr = slice_["manager"]
                print(f"[{mgr}]")
                local = os.path.join(tmp, f"padus_{mgr.lower()}.geojson")
                try:
                    download(slice_["url"], local)
                except httpx.HTTPError as exc:
                    print(f"  skip {mgr}: {exc}")
                    continue
                # Lazy import -- pyogrio is heavy and isn't a runtime dep.
                import pyogrio
                gdf = pyogrio.read_dataframe(local)
                if gdf is None or gdf.empty:
                    print(f"  skip {mgr}: empty")
                    continue
                # Feed shapely features one at a time; pyogrio's
                # iterfeatures keeps memory bounded.
                for feat in simplify_and_clean(gdf.iterfeatures(), mgr):
                    if not first:
                        out.write(",")
                    json.dump(feat, out, separators=(",", ":"))
                    first = False
                    total += 1
                print(f"  +{total:,} cumulative features")
                # Free memory before the next slice. The Federal slice
                # in particular is gigabytes of polygons.
                del gdf
        out.write("]}")
    size_mb = os.path.getsize(OUT_PATH) / 1e6
    print(f"\n[done] {total:,} parcels -> {OUT_PATH} ({size_mb:.1f} MB gz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
