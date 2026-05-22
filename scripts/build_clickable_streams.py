#!/usr/bin/env python3
"""
Build data/nhdplus/clickable_streams.geojson.gz -- the geometry layer of
fishing-relevant streams that become clickable on the map (the
"bluelining" network).

A reach is clickable if ANY of:
  - StreamOrder >= 3 (the geometry base from NHDPlusV2)
  - state-designated trout water (VA / MD / PA, any order)
  - StreamOrder >= 3 tributary of a trout-water COMID (via NHDPlus topo)
  - named river StreamOrder >= 5

Each feature carries:
    comid, levelpathid, gnis_name, streamorder, lengthkm, trout_class

trout_class is one of:
    wild_reproduction, class_a, wilderness, stocked, designated, null

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
import time
from collections import defaultdict

import geopandas as gpd
import httpx
import py7zr
import shapely
from dbfread import DBF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "nhdplus", "clickable_streams.geojson.gz")
S3 = "https://dmap-data-commons-ow.s3.amazonaws.com/NHDPlusV21/Data"

MIN_ORDER = 3
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

# --- PA PASDA trout layers ---
PASDA_BASE = ("https://mapservices.pasda.psu.edu/server/rest/services"
              "/pasda/PAFishBoat/MapServer")
PA_TROUT_LAYERS = [
    {"id": 36, "class": "wild_reproduction",
     "label": "Trout Natural Reproduction with Tributaries"},
    {"id": 9,  "class": "class_a",
     "label": "Class A Streams"},
    {"id": 31, "class": "wilderness",
     "label": "Wilderness Trout Streams"},
    {"id": 3,  "class": "stocked",
     "label": "Stocked Trout Waters"},
]

# --- VA / MD ArcGIS trout endpoints ---
VA_TROUT_URL = (
    "https://services.dwr.virginia.gov/arcgis/rest/services/Public/"
    "WildTroutStreams/MapServer/0/query?where=1%3D1"
)
MD_TROUT_URL = (
    "https://dnr.geodata.md.gov/dnrdata/rest/services/Fisheries/"
    "DesignatedUse_Trout/MapServer/0/query?where=1%3D1"
)

USER_AGENT = "Blueliner/1.0 (+https://blueliner.app)"
REQUEST_TIMEOUT = 20.0
TOTAL_BUDGET = 120.0
MAX_PAGES = 80
SPATIAL_JOIN_BUFFER_DEG = 0.001  # ~100 m


# ──────────────────────── NHDPlus download / parse ────────────────────────

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
    """COMID -> {streamorder, levelpathid, lengthkm, hydroseq, dnhydroseq}."""
    out: dict[int, dict] = {}
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("ComID")
        if comid is None:
            continue
        out[int(comid)] = {
            "streamorder": rec.get("StreamOrde"),
            "levelpathid": int(rec["LevelPathI"]) if rec.get("LevelPathI") else None,
            "lengthkm": float(rec["LengthKM"]) if rec.get("LengthKM") else None,
            "hydroseq": int(rec["Hydroseq"]) if rec.get("Hydroseq") else None,
            "dnhydroseq": int(rec["DnHydroseq"]) if rec.get("DnHydroseq") else None,
        }
    return out


def load_nhd_region(region: dict, tmp: str):
    """Download+extract NHDPlus for one region. Returns (gdf_4326, vaa_dict)."""
    print(f"[{region['id']}] {region['label']}")
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
    gdf = gdf.to_crs(4326)
    return gdf, attrs


# ──────────────────────── ArcGIS keyset pagination ────────────────────────

def _discover_oid_field(client: httpx.Client, layer_url: str) -> str | None:
    """Find the OID field name from the layer metadata, checking actual fields."""
    try:
        r = client.get(layer_url, params={"f": "json"})
        r.raise_for_status()
        meta = r.json()
        declared = meta.get("objectIdField")
        if declared:
            return declared
        field_names = {f["name"] for f in meta.get("fields", [])}
        for cand in ("OBJECTID", "OBJECTID_12", "FID", "ESRI_OID", "objectid"):
            if cand in field_names:
                return cand
        return None
    except Exception:
        return None


def fetch_arcgis_features(query_url: str, page_size: int = 1000) -> list[dict]:
    """Paginate an ArcGIS query endpoint via OBJECTID keyset."""
    from urllib.parse import urlsplit, parse_qs
    split = urlsplit(query_url)
    base = f"{split.scheme}://{split.netloc}{split.path}"
    layer_url = base.rsplit("/query", 1)[0]
    src = {k: v[0] for k, v in parse_qs(split.query).items()}
    user_where = src.get("where", "1=1")
    common = {
        "f": "geojson", "outSR": "4326", "returnGeometry": "true",
        "outFields": src.get("outFields", "*"),
        "resultRecordCount": str(page_size),
    }
    features: list[dict] = []
    deadline = time.monotonic() + TOTAL_BUDGET
    with httpx.Client(timeout=REQUEST_TIMEOUT,
                      headers={"User-Agent": USER_AGENT}) as client:
        oid = _discover_oid_field(client, layer_url)
        last: int | None = None
        for _ in range(MAX_PAGES):
            if time.monotonic() > deadline:
                break
            params = dict(common)
            if oid:
                bound = -1 if last is None else last
                params["where"] = f"({user_where}) AND {oid} > {bound}"
                params["orderByFields"] = f"{oid} ASC"
            else:
                params["where"] = user_where
            resp = client.get(base, params=params)
            resp.raise_for_status()
            batch = resp.json().get("features", [])
            if not batch:
                break
            if not oid:
                features.extend(batch)
                break
            ids = []
            for f in batch:
                p = f.get("properties") or {}
                v = p.get(oid) or f.get("id")
                try:
                    ids.append(int(v))
                except (TypeError, ValueError):
                    pass
            if not ids:
                features.extend(batch)
                break
            mx = max(ids)
            if last is not None and mx <= last:
                break
            features.extend(batch)
            last = mx
            if len(batch) < page_size:
                break
    return features


# ──────────────────────── Trout GIS ingestion ────────────────────────

def fetch_trout_va() -> gpd.GeoDataFrame | None:
    """VA wild trout streams — uses spatial join (VA REACHCODE is
    state-specific, not NHD 14-digit)."""
    print("[trout] Virginia Wild Trout Streams ...")
    feats = fetch_arcgis_features(VA_TROUT_URL)
    if not feats:
        print("  WARNING: no features returned")
        return None
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    print(f"  {len(gdf)} features")
    return gdf


def fetch_trout_md() -> gpd.GeoDataFrame | None:
    """MD designated trout — has ComID for direct key join."""
    print("[trout] Maryland Designated Use Trout ...")
    feats = fetch_arcgis_features(MD_TROUT_URL)
    if not feats:
        print("  WARNING: no features returned")
        return None
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    print(f"  {len(gdf)} features")
    return gdf


def fetch_trout_pa() -> dict[str, gpd.GeoDataFrame]:
    """PA PASDA trout layers — keyed by trout_class."""
    results = {}
    for layer in PA_TROUT_LAYERS:
        url = f"{PASDA_BASE}/{layer['id']}/query?where=1%3D1"
        print(f"[trout] PA {layer['label']} (layer {layer['id']}) ...")
        feats = fetch_arcgis_features(url)
        if not feats:
            print(f"  WARNING: no features for layer {layer['id']}")
            continue
        gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
        print(f"  {len(gdf)} features")
        results[layer["class"]] = gdf
    return results


# ──────────────────────── Join trout to NHD COMIDs ────────────────────────

def trout_comids_md(md_gdf: gpd.GeoDataFrame,
                    nhd_gdf: gpd.GeoDataFrame,
                    all_attrs: dict[int, dict]) -> dict[int, str]:
    """MD: spatial join. MD ComIDs are NHDPlus HR, not V2, so key join
    against our V2 COMIDs won't match."""
    print("  MD spatial join: designated ...")
    out = spatial_join_trout(md_gdf, nhd_gdf, "designated", all_attrs)
    print(f"    {len(out)} COMIDs matched")
    return out


def spatial_join_trout(trout_gdf: gpd.GeoDataFrame,
                       nhd_gdf: gpd.GeoDataFrame,
                       trout_class: str,
                       all_attrs: dict[int, dict]) -> dict[int, str]:
    """Spatial join: buffer trout lines and find overlapping NHD COMIDs."""
    import warnings
    id_col = next(c for c in nhd_gdf.columns if c.lower() == "comid")
    nhd_sub = nhd_gdf[[id_col, "geometry"]].copy()
    nhd_sub = nhd_sub[~nhd_sub.geometry.isna() & ~nhd_sub.geometry.is_empty]

    trout_buf = trout_gdf.copy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        trout_buf["geometry"] = trout_buf.geometry.buffer(SPATIAL_JOIN_BUFFER_DEG)
    trout_buf = trout_buf[~trout_buf.geometry.isna() & ~trout_buf.geometry.is_empty]

    joined = gpd.sjoin(nhd_sub, trout_buf, how="inner", predicate="intersects")
    comids = set(int(c) for c in joined[id_col].unique() if c is not None)
    out = {c: trout_class for c in comids if c in all_attrs}
    return out


# ──────────────────────── NHDPlus topology ────────────────────────

def build_upstream_graph(all_attrs: dict[int, dict]) -> dict[int, list[int]]:
    """Hydroseq -> list of upstream COMIDs (those whose DnHydroseq == this Hydroseq)."""
    hs_to_comid: dict[int, int] = {}
    for comid, a in all_attrs.items():
        hs = a.get("hydroseq")
        if hs:
            hs_to_comid[hs] = comid

    upstream: dict[int, list[int]] = defaultdict(list)
    for comid, a in all_attrs.items():
        dnhs = a.get("dnhydroseq")
        if dnhs and dnhs in hs_to_comid:
            downstream_comid = hs_to_comid[dnhs]
            upstream[downstream_comid].append(comid)
    return upstream


def collect_upstream_tributaries(trout_comids: set[int],
                                 upstream_graph: dict[int, list[int]],
                                 all_attrs: dict[int, dict]) -> set[int]:
    """Walk upstream from trout COMIDs, collect order >= 3 tributaries."""
    result: set[int] = set()
    visited: set[int] = set()
    stack = list(trout_comids)
    while stack:
        comid = stack.pop()
        if comid in visited:
            continue
        visited.add(comid)
        for up_comid in upstream_graph.get(comid, []):
            a = all_attrs.get(up_comid)
            if not a:
                continue
            order = a.get("streamorder")
            if order is not None and order >= MIN_ORDER:
                result.add(up_comid)
                stack.append(up_comid)
    return result


# ──────────────────────── Feature assembly ────────────────────────

def build_feature(comid: int, row, gnis_col: str | None,
                  attrs: dict, trout_class: str | None) -> dict | None:
    """Build a single GeoJSON feature dict."""
    a = attrs.get(comid)
    if not a:
        return None
    geom = row.geometry if hasattr(row, "geometry") else row.get("geometry")
    if geom is None or geom.is_empty:
        return None
    geom = geom.simplify(SIMPLIFY_TOL, preserve_topology=False)
    geom = shapely.set_precision(geom, COORD_GRID)
    if geom.is_empty:
        return None
    name = row[gnis_col] if gnis_col and gnis_col in (row.index if hasattr(row, "index") else []) else None
    return {
        "type": "Feature",
        "geometry": shapely.geometry.mapping(geom),
        "properties": {
            "comid": comid,
            "levelpathid": a["levelpathid"],
            "gnis_name": (str(name).strip() or None) if name else None,
            "streamorder": int(a["streamorder"]) if a["streamorder"] else None,
            "lengthkm": a["lengthkm"],
            "trout_class": trout_class,
        },
    }


# ──────────────────────── PA validation ────────────────────────

PA_WILD_TROUT_VALIDATION = [
    "Penns Creek", "Spring Creek", "Kettle Creek", "Slate Run",
    "Pine Creek", "Loyalsock Creek", "Young Womans Creek",
    "Fishing Creek", "Cedar Run",
    "Cross Fork", "Elk Creek", "First Fork Sinnemahoning Creek",
    "Little Pine Creek", "Spruce Creek", "Bald Eagle Creek",
    "East Branch Fishing Creek",
]


def validate_pa_coverage(clickable_comids: set[int],
                         trout_comids: dict[int, str],
                         all_attrs: dict[int, dict],
                         nhd_gdfs: list[gpd.GeoDataFrame]) -> None:
    """Check that well-known PA wild-trout streams are clickable."""
    print("\n── PA wild-trout validation ──")
    name_to_comids: dict[str, set[int]] = defaultdict(set)
    for gdf in nhd_gdfs:
        gnis_col = next((c for c in gdf.columns if c.lower() == "gnis_name"), None)
        id_col = next(c for c in gdf.columns if c.lower() == "comid")
        if not gnis_col:
            continue
        for _, row in gdf.iterrows():
            nm = row[gnis_col]
            if nm and str(nm).strip():
                name_to_comids[str(nm).strip()].add(int(row[id_col]))

    hits = 0
    misses = []
    for name in PA_WILD_TROUT_VALIDATION:
        comids = name_to_comids.get(name, set())
        found_clickable = any(c in clickable_comids for c in comids)
        found_trout = any(c in trout_comids for c in comids)
        if found_clickable:
            tag = " [trout]" if found_trout else ""
            print(f"  OK   {name} ({len(comids)} COMIDs){tag}")
            hits += 1
        else:
            misses.append(name)
            nhd_count = len(comids)
            print(f"  MISS {name} ({nhd_count} NHD COMIDs, 0 clickable)")

    total = len(PA_WILD_TROUT_VALIDATION)
    print(f"\nPA coverage: {hits}/{total} "
          f"({100 * hits / total:.0f}%)")
    if misses:
        print(f"Missed: {', '.join(misses)}")


# ──────────────────────── Main ────────────────────────

def main() -> int:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)

    # ── Step 1: Download NHDPlus regions ──
    all_attrs: dict[int, dict] = {}
    nhd_gdfs: list[gpd.GeoDataFrame] = []
    with tempfile.TemporaryDirectory() as tmp:
        for region in REGIONS:
            gdf, attrs = load_nhd_region(region, tmp)
            nhd_gdfs.append(gdf)
            all_attrs.update(attrs)

    print(f"\nNHDPlus: {len(all_attrs):,} COMIDs across {len(REGIONS)} regions")

    # ── Step 2: Fetch state trout GIS ──
    print()
    va_gdf = fetch_trout_va()
    md_gdf = fetch_trout_md()
    pa_gdfs = fetch_trout_pa()

    # ── Step 3: Join trout to NHD COMIDs ──
    print("\n── Joining trout data to NHD COMIDs ──")
    trout_comids: dict[int, str] = {}  # comid -> trout_class (first wins)

    nhd_combined = gpd.GeoDataFrame(
        gpd.pd.concat(nhd_gdfs, ignore_index=True),
        crs="EPSG:4326"
    )

    # MD: spatial join (MD ComIDs are NHDPlus HR, not V2)
    if md_gdf is not None:
        md_trout = trout_comids_md(md_gdf, nhd_combined, all_attrs)
        for c, cls in md_trout.items():
            trout_comids.setdefault(c, cls)

    # VA: spatial join (VA REACHCODE is state-specific, not NHD)
    if va_gdf is not None:
        print("  VA spatial join: wild_reproduction ...")
        va_trout = spatial_join_trout(
            va_gdf, nhd_combined, "wild_reproduction", all_attrs)
        print(f"    {len(va_trout)} COMIDs matched")
        for c, cls in va_trout.items():
            trout_comids.setdefault(c, cls)

    # PA: spatial join for each layer (no NHD keys in PASDA)
    for trout_class, pa_gdf in pa_gdfs.items():
        print(f"  PA spatial join: {trout_class} ...")
        pa_trout = spatial_join_trout(pa_gdf, nhd_combined, trout_class, all_attrs)
        print(f"    {len(pa_trout)} COMIDs matched")
        for c, cls in pa_trout.items():
            trout_comids.setdefault(c, cls)

    print(f"\nTotal trout COMIDs: {len(trout_comids):,}")
    by_class = defaultdict(int)
    for cls in trout_comids.values():
        by_class[cls] += 1
    for cls, n in sorted(by_class.items()):
        print(f"  {cls}: {n:,}")

    # ── Step 4: Compute upstream tributaries ──
    print("\n── Computing upstream tributaries (order >= 3) ──")
    upstream_graph = build_upstream_graph(all_attrs)
    trib_comids = collect_upstream_tributaries(
        set(trout_comids.keys()), upstream_graph, all_attrs
    )
    print(f"  {len(trib_comids):,} upstream tributary COMIDs (order >= {MIN_ORDER})")

    # ── Step 5: Build the clickable set ──
    # Clickable = order>=3 base  ∪  trout COMIDs (any order)  ∪  trout tributaries
    clickable_comids: set[int] = set()
    for comid, a in all_attrs.items():
        order = a.get("streamorder")
        if order is not None and order >= MIN_ORDER:
            clickable_comids.add(comid)
    clickable_comids.update(trout_comids.keys())
    clickable_comids.update(trib_comids)

    # ── Step 6: Emit features ──
    print(f"\n── Building features ({len(clickable_comids):,} clickable COMIDs) ──")
    id_cols = []
    gnis_cols = []
    for gdf in nhd_gdfs:
        id_cols.append(next(c for c in gdf.columns if c.lower() == "comid"))
        gnis_cols.append(next((c for c in gdf.columns if c.lower() == "gnis_name"), None))

    emitted: set[int] = set()
    all_feats: list[dict] = []
    for gdf, id_col, gnis_col in zip(nhd_gdfs, id_cols, gnis_cols):
        for _, row in gdf.iterrows():
            comid = int(row[id_col])
            if comid in emitted or comid not in clickable_comids:
                continue
            tc = trout_comids.get(comid)
            feat = build_feature(comid, row, gnis_col, all_attrs, tc)
            if feat:
                all_feats.append(feat)
                emitted.add(comid)

    fc = {"type": "FeatureCollection", "features": all_feats}
    body = json.dumps(fc, separators=(",", ":"))
    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as f:
        f.write(body)
    size = os.path.getsize(OUT_PATH)
    print(f"\n[done] {len(all_feats):,} flowlines -> {OUT_PATH} "
          f"({size / 1e6:.1f} MB gz, {len(body) / 1e6:.1f} MB raw)")

    # ── Step 7: PA validation ──
    validate_pa_coverage(clickable_comids, trout_comids, all_attrs, nhd_gdfs)

    return 0


if __name__ == "__main__":
    sys.exit(main())
