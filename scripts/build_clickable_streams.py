#!/usr/bin/env python3
"""
One-time prep: build data/nhdplus/clickable_streams.geojson.gz -- the
geometry layer of fishing-relevant streams that become clickable on the
map (the "bluelining" network).

For each focused NHDPlusV2 region we download the flowline geometry
(NHDSnapshot / NHDFlowline.shp) and the routing attributes
(NHDPlusAttributes / PlusFlowlineVAA.dbf), keep flowlines at
StreamOrder >= MIN_ORDER, simplify, and emit one gzipped GeoJSON
FeatureCollection in EPSG:4326. Each feature carries:

    comid, levelpathid, gnis_name, streamorder, lengthkm

This is the size-validated base of the clickable network (the whole
Monocacy and thousands of named tributaries). A later pass folds in
state-designated wild-trout streams (PASDA / VA DWR / MD DNR) so small
designated waters below MIN_ORDER are also included, with trout class.

Dev dependencies (NOT in runtime requirements.txt):
    pip install dbfread py7zr httpx geopandas shapely

Run from repo root:
    python scripts/build_clickable_streams.py
"""

import glob
import gzip
import json
import os
import sys
import tempfile

import geopandas as gpd
import httpx
import py7zr
import shapely
from dbfread import DBF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "nhdplus", "clickable_streams.geojson.gz")
S3 = "https://dmap-data-commons-ow.s3.amazonaws.com/NHDPlusV21/Data"

# Strahler stream order floor for the geometry base. >=3 keeps real
# streams (incl. mid-size unpressured tributaries) while dropping the
# order-1/2 trickles that would crowd the map; designated wild-trout
# streams below this are added by the trout-GIS pass.
MIN_ORDER = 3
# Geometry simplification: Douglas-Peucker tolerance (~30 m) + coordinate
# snapping (~1 m). Plenty of detail for web display; shrinks the bundle.
SIMPLIFY_TOL = 0.0003
COORD_GRID = 1e-5

REGIONS = [
    {"id": "MA_02", "label": "Mid-Atlantic (HUC-02)",
     "snap": f"{S3}/NHDPlusMA/NHDPlusV21_MA_02_NHDSnapshot_04.7z",
     "attr": f"{S3}/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_09.7z"},
    {"id": "MS_05", "label": "Ohio (HUC-05)",
     "snap": f"{S3}/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDSnapshot_06.7z",
     "attr": f"{S3}/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDPlusAttributes_09.7z"},
]


def download(url: str, dest: str) -> None:
    sys.stdout.write(f"  fetch {os.path.basename(url)} ... ")
    sys.stdout.flush()
    with httpx.stream("GET", url, timeout=180.0, follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"{os.path.getsize(dest) / 1e6:.0f} MB")


def extract(archive: str, workdir: str, suffixes: list[str]) -> None:
    with py7zr.SevenZipFile(archive, mode="r") as z:
        targets = [n for n in z.getnames()
                   if any(n.endswith(s) for s in suffixes)]
        if not targets:
            raise RuntimeError(f"none of {suffixes} in {archive}")
        z.extract(path=workdir, targets=targets)


def vaa_attrs(dbf_path: str) -> dict[int, dict]:
    """COMID -> {streamorder, levelpathid, lengthkm} from PlusFlowlineVAA."""
    out: dict[int, dict] = {}
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("ComID")
        if comid is None:
            continue
        out[int(comid)] = {
            "streamorder": rec.get("StreamOrde"),
            "levelpathid": int(rec["LevelPathI"]) if rec.get("LevelPathI") else None,
            "lengthkm": float(rec["LengthKM"]) if rec.get("LengthKM") else None,
        }
    return out


def region_features(region: dict, tmp: str) -> list[dict]:
    print(f"[{region['id']}] {region['label']}")
    # Per-region extraction dir so the globs below can't pick up another
    # region's identically-named NHDFlowline.shp / PlusFlowlineVAA.dbf.
    work = os.path.join(tmp, region["id"])
    os.makedirs(work, exist_ok=True)
    snap = os.path.join(work, "snap.7z")
    attr = os.path.join(work, "attr.7z")
    download(region["snap"], snap)
    download(region["attr"], attr)

    print("  extracting flowline geometry + VAA ...")
    extract(snap, work, ["NHDFlowline.shp", "NHDFlowline.shx",
                         "NHDFlowline.dbf", "NHDFlowline.prj"])
    extract(attr, work, ["PlusFlowlineVAA.dbf"])
    shp = glob.glob(f"{work}/**/NHDFlowline.shp", recursive=True)[0]
    vaa_dbf = glob.glob(f"{work}/**/PlusFlowlineVAA.dbf", recursive=True)[0]

    attrs = vaa_attrs(vaa_dbf)
    gdf = gpd.read_file(shp)
    id_col = next(c for c in gdf.columns if c.lower() == "comid")
    gnis_col = next((c for c in gdf.columns if c.lower() == "gnis_name"), None)
    gdf = gdf.to_crs(4326)

    feats: list[dict] = []
    kept = 0
    for _, row in gdf.iterrows():
        comid = int(row[id_col])
        a = attrs.get(comid)
        if not a:
            continue
        order = a["streamorder"]
        if order is None or order < MIN_ORDER:
            continue
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        geom = geom.simplify(SIMPLIFY_TOL, preserve_topology=False)
        geom = shapely.set_precision(geom, COORD_GRID)
        if geom.is_empty:
            continue
        name = row[gnis_col] if gnis_col else None
        feats.append({
            "type": "Feature",
            "geometry": shapely.geometry.mapping(geom),
            "properties": {
                "comid": comid,
                "levelpathid": a["levelpathid"],
                "gnis_name": (str(name).strip() or None) if name else None,
                "streamorder": int(order),
                "lengthkm": a["lengthkm"],
            },
        })
        kept += 1
    print(f"  kept {kept:,} flowlines (order >= {MIN_ORDER})")
    return feats


def main() -> int:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    all_feats: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        for region in REGIONS:
            all_feats.extend(region_features(region, tmp))

    fc = {"type": "FeatureCollection", "features": all_feats}
    body = json.dumps(fc, separators=(",", ":"))
    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as f:
        f.write(body)
    size = os.path.getsize(OUT_PATH)
    print(f"\n[done] {len(all_feats):,} flowlines -> {OUT_PATH} "
          f"({size / 1e6:.1f} MB gz, {len(body) / 1e6:.1f} MB raw)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
