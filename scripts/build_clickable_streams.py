#!/usr/bin/env python3
"""
Build data/nhdplus/clickable_streams.geojson.gz -- the geometry layer of
fishing-relevant streams that become clickable on the map (the
"bluelining" network).

A reach is clickable if EITHER:
  - StreamOrder >= 3 (the geometry base from NHDPlusV2), or
  - it's a state-designated trout water (VA / MD / PA, any order).

(An earlier "order >= 3 tributary of a trout water" rule was a no-op: such
tributaries are order >= 3, so they're already in the base set. Dropping that
topology pass is output-identical and is what lets the build stream one VPU at
a time -- see main().)

Each feature carries:
    comid, levelpathid, gnis_name, streamorder, lengthkm, trout_class

trout_class is one of:
    wild_reproduction, class_a, wilderness, stocked, designated, null

Dev dependencies (NOT in runtime requirements.txt):
    pip install dbfread py7zr httpx geopandas shapely

Run from repo root:
    python scripts/build_clickable_streams.py                  # all 21 VPUs (lower 48)
    python scripts/build_clickable_streams.py --regions 02,05   # a subset
    python scripts/build_clickable_streams.py --skip-download   # reuse cache

The build covers the lower-48 NHDPlusV2 Vector Processing Units (the `VPUS`
table); archive URLs are discovered from the S3 listing at build time so the
per-release vintage suffixes never need hardcoding. It is fully VPU-streaming:
one region at a time is downloaded, joined to trout layers, emitted to the gzip
output, and then its extract is deleted before the next -- so BOTH peak memory
(~one region's attrs + geometry) and peak disk (~one region's archives) stay
bounded regardless of how many regions there are. That keeps a full lower-48
build inside a free standard CI runner's ~14 GB disk. Archives extract into
--cache-dir (default $TMPDIR/blueliner_nhd_cache); --skip-download reuses a
prior extract and --keep-extracts disables the delete-after-each-region.
"""

import argparse
import glob
import gzip
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from urllib.parse import quote

import geopandas as gpd
import httpx
import py7zr
import shapely
from dbfread import DBF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "nhdplus", "clickable_streams.geojson.gz")
S3_BUCKET = "https://dmap-data-commons-ow.s3.amazonaws.com"

MIN_ORDER = 3
SIMPLIFY_TOL = 0.0003
COORD_GRID = 1e-5

# ── NHDPlusV2 Vector Processing Units (VPUs) for the lower 48 ──
# Each VPU lives under a Drainage Area directory `NHDPlus<DA>/`; multi-VPU
# DAs additionally nest a `NHDPlus<VPU>/` subdir. The archive *vintage*
# suffixes (`_NHDSnapshot_<n>` / `_NHDPlusAttributes_<n>`) differ per VPU and
# get bumped on re-release, so rather than hardcode (and rot) them we resolve
# the real archive URLs from the S3 bucket listing at build time
# (discover_archive). Adding/auditing a region is then just this table.
# (Caribbean/Hawaii/Pacific-Islands DAs are intentionally excluded.)
_MULTI_VPU_DA = {"SA", "MS", "CO"}

VPUS = [
    ("01", "NE", "Northeast"),
    ("02", "MA", "Mid-Atlantic"),
    ("03N", "SA", "South Atlantic North"),
    ("03S", "SA", "South Atlantic South"),
    ("03W", "SA", "South Atlantic West"),
    ("04", "GL", "Great Lakes"),
    ("05", "MS", "Ohio"),
    ("06", "MS", "Tennessee"),
    ("07", "MS", "Upper Mississippi"),
    ("08", "MS", "Lower Mississippi"),
    ("09", "SR", "Souris-Red-Rainy"),
    ("10L", "MS", "Lower Missouri"),
    ("10U", "MS", "Upper Missouri"),
    ("11", "MS", "Arkansas-White-Red"),
    ("12", "TX", "Texas-Gulf"),
    ("13", "RG", "Rio Grande"),
    ("14", "CO", "Upper Colorado"),
    ("15", "CO", "Lower Colorado"),
    ("16", "GB", "Great Basin"),
    ("17", "PN", "Pacific Northwest"),
    ("18", "CA", "California"),
]

# Region descriptors consumed by the build. `snap`/`attr` URLs are resolved
# lazily (discover_archive) since they carry per-release vintage suffixes.
REGIONS = [
    {"id": vpu, "da": da, "vpu": vpu, "label": f"{label} (VPU {vpu})"}
    for vpu, da, label in VPUS
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
# Committed COMID seed used only when MD_TROUT_URL is unreachable (its ArcGIS
# server flaps). NHDPlusV2 COMIDs are stable, so tagging from a prior live
# capture is equivalent until MD changes its designations; regenerate from a
# fresh live build when the endpoint recovers. (Lives in data/nhdplus/, not
# data/trout/ -- the latter holds runtime GeoJSON bundles for trout.py; this is
# a builder-only COMID list.)
MD_SEED_PATH = os.path.join(ROOT, "data", "nhdplus", "MD_designated_comids.json")

# --- Single-bucket trout layers (Northeast + Appalachian) ---
# Each layer maps wholesale to one trout_class (the agency publishes a single
# wild- or stocked-trout layer, with no per-feature category to split on), so
# they reuse the existing wild_reproduction/stocked classes -> the frontend
# (streams.ts) needs no change; only the tile data widens. Source CRS varies
# (NJ 3424, VT/ME State Plane/UTM, MA 26986, WV 3857) but fetch_arcgis_features
# always requests outSR=4326, so the server reprojects for us.
# NOTE: VT "Brook Trout Waters" is EBTJV catchment polygons (subwatersheds with
# brook trout), not stream centerlines -- the spatial join tags every NHD
# flowline inside each polygon, so VT renders coarser than the line-based states.
SINGLE_BUCKET_TROUT_LAYERS = [
    {"state": "NJ", "class": "stocked",
     "label": "NJ Trout Stocked Streams",
     "url": ("https://mapsdep.nj.gov/arcgis/rest/services/Features/"
             "Environmental_admin/MapServer/35/query?where=1%3D1")},
    {"state": "VT", "class": "wild_reproduction",
     "label": "VT Brook Trout Waters",
     "url": ("https://anrmaps.vermont.gov/arcgis/rest/services/map_services/"
             "MAP_ANR_ANRATLASFISHWILDLIFE_WM_NOCACHE/MapServer/49/"
             "query?where=1%3D1")},
    {"state": "MA", "class": "wild_reproduction",
     "label": "MA Coldwater Fisheries Resources",
     "url": ("https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/"
             "AGOL/DFW_CFR/FeatureServer/0/query?where=1%3D1")},
    # ME (Wild Brook Trout Priority Conservation Areas) deferred: Maine moved its
    # gis.maine.gov /ifw REST services behind MaineIT GIS Enterprise Portal auth,
    # so they're no longer anonymously queryable. Keeping it out of the list so a
    # --require-trout release isn't blocked by an endpoint we can't reach.
    {"state": "WV", "class": "stocked",
     "label": "WV Stocked Trout Streams",
     "url": ("https://services.wvgis.wvu.edu/arcgis/rest/services/Applications/"
             "dnrRec_fishing/MapServer/4/query?where=1%3D1")},
]

# --- NY DEC inland trout stream reaches (multi-bucket) ---
# Layer 0 of dil_water_activities carries the Trout Stream Management Plan
# categorization in field MGMTCAT; we fold its 5 reach categories into our two
# buckets. "Other" = wild reaches with catch-and-release regs that aren't ranked
# Quality/Premier, so they ride with wild rather than being dropped.
NY_TROUT_URL = ("https://gisservices.dec.ny.gov/arcgis/rest/services/dil/"
                "dil_water_activities/MapServer/0/query?where=1%3D1")
NY_MGMTCAT_CLASS = {
    "Stocked": "stocked",
    "Stocked-Extended": "stocked",
    "Wild-Quality": "wild_reproduction",
    "Wild-Premier": "wild_reproduction",
    "Other": "wild_reproduction",
}

USER_AGENT = "Blueliner/1.0 (+https://blueliner.app)"
REQUEST_TIMEOUT = 20.0
TOTAL_BUDGET = 120.0
MAX_PAGES = 80
MAX_RETRIES = 5  # per-request retries (transport errors + 5xx) before giving up
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


def _list_s3_keys(prefix: str) -> list[str]:
    """Anonymous S3 list-type=2 for object keys under `prefix`. Callers pass a
    narrow prefix (a specific component filename stem) so a single page of
    results suffices -- no continuation-token handling needed."""
    url = f"{S3_BUCKET}/?list-type=2&prefix={quote(prefix, safe='')}"
    with httpx.Client(timeout=60.0,
                      headers={"User-Agent": USER_AGENT}) as client:
        r = client.get(url)
        r.raise_for_status()
        return re.findall(r"<Key>([^<]+)</Key>", r.text)


def discover_archive(da: str, vpu: str, component: str) -> str:
    """Resolve the full URL of a region's `component` archive (NHDSnapshot or
    NHDPlusAttributes) from the live S3 listing, picking the highest vintage.
    The vintage suffix (`_<n>.7z`) rots across releases, so we never hardcode
    it. Excludes the same-prefixed FGDB variant (e.g. NHDSnapshotFGDB)."""
    subdir = f"NHDPlus{vpu}/" if da in _MULTI_VPU_DA else ""
    stem = (f"NHDPlusV21/Data/NHDPlus{da}/{subdir}"
            f"NHDPlusV21_{da}_{vpu}_{component}")
    keys = _list_s3_keys(stem)
    pat = re.compile(rf"_{re.escape(component)}_(\d+)\.7z$")
    cands = [(int(m.group(1)), k) for k in keys if (m := pat.search(k))]
    if not cands:
        raise RuntimeError(
            f"no {component} archive found under {stem} (keys: {keys})")
    _, key = max(cands)
    return f"{S3_BUCKET}/{key}"


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


def prepare_region(region: dict, cache_dir: str, skip_download: bool) -> tuple[str, str]:
    """Ensure one region's NHDFlowline shapefile + PlusFlowlineVAA.dbf are
    extracted under `cache_dir/<id>`, downloading the .7z archives unless a
    cached extract is already present (with --skip-download). Returns the
    (shapefile, vaa_dbf) paths so both passes can re-read geometry from disk
    without holding every region in memory at once."""
    work = os.path.join(cache_dir, region["id"])
    os.makedirs(work, exist_ok=True)
    shp_hits = glob.glob(f"{work}/**/NHDFlowline.shp", recursive=True)
    vaa_hits = glob.glob(f"{work}/**/PlusFlowlineVAA.dbf", recursive=True)
    if skip_download and shp_hits and vaa_hits:
        print(f"[{region['id']}] {region['label']} — reusing cached extract")
        return shp_hits[0], vaa_hits[0], {}

    print(f"[{region['id']}] {region['label']}")
    snap_url = discover_archive(region["da"], region["vpu"], "NHDSnapshot")
    attr_url = discover_archive(region["da"], region["vpu"], "NHDPlusAttributes")
    snap = os.path.join(work, "snap.7z")
    attr = os.path.join(work, "attr.7z")
    download(snap_url, snap)
    download(attr_url, attr)
    print("  extracting flowline geometry + VAA ...")
    extract(snap, work, ["NHDFlowline.shp", "NHDFlowline.shx",
                         "NHDFlowline.dbf", "NHDFlowline.prj"])
    extract(attr, work, ["PlusFlowlineVAA.dbf"])
    shp = glob.glob(f"{work}/**/NHDFlowline.shp", recursive=True)[0]
    vaa_dbf = glob.glob(f"{work}/**/PlusFlowlineVAA.dbf", recursive=True)[0]
    return shp, vaa_dbf, {"snap": snap_url, "attr": attr_url}


def read_region_gdf(shp: str) -> gpd.GeoDataFrame:
    """Read one region's flowline geometry, reprojected to EPSG:4326. Read
    lazily per-region (and discarded after use) to bound peak memory."""
    gdf = gpd.read_file(shp)
    return gdf.to_crs(4326)


def write_manifest(path: str, region_ids: list[str], resolved: dict,
                   feature_count: int, trout_class_counts: dict,
                   trout_status: dict) -> None:
    """Provenance sidecar for a built artifact: which regions, which exact NHD
    archive vintages were resolved (so a release is reproducible / auditable),
    the feature count, the trout-class histogram, and which state trout sources
    were reachable (so a degraded build is visible). Answers 'what is live?'
    when shipped alongside the .geojson.gz / .pmtiles."""
    import datetime
    manifest = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                 .isoformat(timespec="seconds"),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "builder": "build_clickable_streams.py",
        "regions": region_ids,
        "feature_count": feature_count,
        "trout_class_counts": dict(sorted(trout_class_counts.items())),
        "trout_sources": dict(sorted(trout_status.items())),
        "archives": resolved,  # {region_id: {snap: url, attr: url}}
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


# ──────────────────────── ArcGIS keyset pagination ────────────────────────

def _http_get_retry(client: httpx.Client, url: str, params: dict,
                    deadline: float) -> httpx.Response:
    """GET with exponential backoff on transport errors and 5xx responses.

    State GIS servers (e.g. MD's DNR ArcGIS) intermittently refuse connections
    or 503 under load; a single blip shouldn't abort a multi-region build. Gives
    up after MAX_RETRIES or when the next backoff would pass `deadline`, raising
    the last error so the caller can decide whether to degrade or fail."""
    err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, params=params)
            if resp.status_code < 500:
                return resp
            err = httpx.HTTPStatusError(
                f"server returned {resp.status_code}",
                request=resp.request, response=resp)
        except httpx.TransportError as e:
            err = e
        wait = min(2 ** attempt, 16)
        if attempt >= MAX_RETRIES or time.monotonic() + wait > deadline:
            break
        print(f"    retry {attempt}/{MAX_RETRIES} after {err} (waiting {wait}s)")
        time.sleep(wait)
    raise err  # type: ignore[misc]


def _discover_oid_field(client: httpx.Client, layer_url: str,
                        deadline: float) -> str | None:
    """Find the OID field name from the layer metadata, checking actual fields."""
    try:
        r = _http_get_retry(client, layer_url, {"f": "json"}, deadline)
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


def _strip_z(coords):
    """Recursively drop any 3rd+ ordinate (Z/M) from a GeoJSON coordinate array.
    ArcGIS MapServers that ignore returnZ (e.g. NY's 10.91 server) emit
    [x, y, null] positions; the null Z makes shapely raise float(None) during
    GeoDataFrame.from_features. Keeping only x,y fixes it and is a no-op for
    coordinates that are already 2D."""
    if not isinstance(coords, (list, tuple)) or not coords:
        return coords
    if isinstance(coords[0], (list, tuple)):
        return [_strip_z(c) for c in coords]
    return list(coords[:2])  # leaf position -> [x, y]


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
        # Drop Z/M ordinates: layers flagged hasZ/hasM (e.g. NY's PolylineZM
        # reaches) otherwise emit null Z values in the GeoJSON, which shapely
        # parses as float(None). No-op for plain 2D layers.
        "returnZ": "false", "returnM": "false",
        "outFields": src.get("outFields", "*"),
        "resultRecordCount": str(page_size),
    }
    features: list[dict] = []
    deadline = time.monotonic() + TOTAL_BUDGET
    with httpx.Client(timeout=REQUEST_TIMEOUT,
                      headers={"User-Agent": USER_AGENT}) as client:
        oid = _discover_oid_field(client, layer_url, deadline)
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
            resp = _http_get_retry(client, base, params, deadline)
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
    # Force 2D: some servers ignore returnZ and emit null Z ordinates that
    # break shapely. Safe no-op for already-2D geometry.
    for f in features:
        g = f.get("geometry")
        if g and g.get("coordinates") is not None:
            g["coordinates"] = _strip_z(g["coordinates"])
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


def load_md_seed() -> tuple[str, set[int]] | None:
    """Fallback for when the live MD endpoint is down: load the committed
    (trout_class, {COMIDs}) capture. Returns None if the seed file is absent."""
    if not os.path.exists(MD_SEED_PATH):
        return None
    with open(MD_SEED_PATH, encoding="utf-8") as f:
        seed = json.load(f)
    comids = {int(c) for c in seed.get("comids", [])}
    if not comids:
        return None
    print(f"  [trout] MD seed: {len(comids):,} '{seed['trout_class']}' COMIDs "
          f"from {seed.get('captured_from', '?')}")
    return seed["trout_class"], comids


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


def fetch_trout_ne(spec: dict) -> gpd.GeoDataFrame | None:
    """A single-bucket Northeast trout layer (whole layer -> spec['class'])."""
    print(f"[trout] {spec['label']} ...")
    feats = fetch_arcgis_features(spec["url"])
    if not feats:
        print("  WARNING: no features returned")
        return None
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    print(f"  {len(gdf)} features")
    return gdf


def fetch_trout_ny() -> dict[str, gpd.GeoDataFrame]:
    """NY DEC inland trout stream reaches, split by MGMTCAT into wild/stocked."""
    print("[trout] NY Inland Trout Stream Fishing ...")
    feats = fetch_arcgis_features(NY_TROUT_URL)
    if not feats:
        print("  WARNING: no features returned")
        return {}
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    if "MGMTCAT" not in gdf.columns:
        print("  WARNING: MGMTCAT field absent; skipping NY")
        return {}
    gdf["_cls"] = gdf["MGMTCAT"].map(NY_MGMTCAT_CLASS)
    # Rows with an unmapped MGMTCAT drop out of the groupby (NaN key).
    results: dict[str, gpd.GeoDataFrame] = {}
    for cls, sub in gdf.groupby("_cls"):
        results[cls] = sub.drop(columns="_cls")
        print(f"  {cls}: {len(sub)} features")
    return results


# ──────────────────────── Join trout to NHD COMIDs ────────────────────────

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


def _bbox_overlap(a, b) -> bool:
    """True if two (minx, miny, maxx, maxy) bounds intersect. Used to skip
    spatial joins between a state's trout layer and regions it can't touch --
    each state overlaps only 1-2 of the ~18 NHDPlus regions, so this is what
    keeps per-state joins from scaling with national geometry."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


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
    raw = row[gnis_col] if gnis_col and gnis_col in (row.index if hasattr(row, "index") else []) else None
    # raw is a pandas cell: a real string, or NaN for an unnamed reach. NaN is
    # truthy and str()s to the literal "nan", so guard with `raw == raw` (only
    # False for NaN) before stringifying -- otherwise unnamed reaches ship a
    # "nan" gnis_name that the client groups into one giant pseudo-river.
    name = None
    if raw is not None and raw == raw:
        s = str(raw).strip()
        if s and s.lower() != "nan":
            name = s
    return {
        "type": "Feature",
        "geometry": shapely.geometry.mapping(geom),
        "properties": {
            "comid": comid,
            "levelpathid": a["levelpathid"],
            "gnis_name": name,
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
                         name_to_comids: dict[str, set[int]]) -> None:
    """Check that well-known PA wild-trout streams are clickable. `name_to_comids`
    is collected during the emit pass (for the validation names only) so we don't
    retain every region's GeoDataFrame just to validate."""
    print("\n── PA wild-trout validation ──")
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

def parse_args(argv: list[str]) -> argparse.Namespace:
    ids = ",".join(r["id"] for r in REGIONS)
    p = argparse.ArgumentParser(
        description="Build data/nhdplus/clickable_streams.geojson.gz")
    p.add_argument("--regions", default="",
                   help=f"Comma-separated region ids to build (default: all). "
                        f"Available: {ids}")
    p.add_argument("--cache-dir",
                   default=os.path.join(tempfile.gettempdir(),
                                        "blueliner_nhd_cache"),
                   help="Where NHDPlus .7z archives are downloaded + extracted. "
                        "Persists across runs so --skip-download can reuse them.")
    p.add_argument("--skip-download", action="store_true",
                   help="Reuse already-extracted archives in --cache-dir "
                        "instead of re-fetching (much faster on repeat runs). "
                        "Implies --keep-extracts.")
    p.add_argument("--keep-extracts", action="store_true",
                   help="Don't delete each region's extract after emitting it. "
                        "Default is to delete (bounds peak disk to ~one region) "
                        "so a full lower-48 build fits a small CI runner.")
    p.add_argument("--require-trout", action="store_true",
                   help="Fail (exit 3) if any state trout source can't be "
                        "fetched, instead of degrading to untagged geometry. "
                        "Use for canonical/published release builds.")
    p.add_argument("--manifest", default="",
                   help="Also write a provenance manifest JSON (regions, "
                        "resolved NHD archive vintages, feature count) here.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    os.makedirs(args.cache_dir, exist_ok=True)

    by_id = {r["id"]: r for r in REGIONS}
    if args.regions.strip():
        want = [s.strip() for s in args.regions.split(",") if s.strip()]
        missing = [w for w in want if w not in by_id]
        if missing:
            print(f"unknown region id(s): {', '.join(missing)}; "
                  f"available: {', '.join(by_id)}", file=sys.stderr)
            return 2
        regions = [by_id[w] for w in want]
    else:
        regions = REGIONS
    print(f"Building {len(regions)} region(s): "
          f"{', '.join(r['id'] for r in regions)}")
    print(f"NHDPlus cache: {args.cache_dir}\n")

    # ── Step 1: Fetch state trout GIS (small; held in memory once) ──
    # These are third-party state ArcGIS servers that occasionally go down. After
    # _http_get_retry has exhausted its backoff, a source is reported unreachable
    # rather than aborting the whole build: the clickable geometry still ships, it
    # just isn't trout-tagged for that state. A canonical release should pass
    # --require-trout so an incomplete artifact never gets published silently.
    trout_status: dict[str, str] = {}  # source label -> "ok" | "unreachable"

    def fetch_source(label: str, fn):
        try:
            result = fn()
            trout_status[label] = "ok"
            return result
        except Exception as e:  # transport/HTTP error after retries
            print(f"  WARNING: {label} trout source unreachable: {e}")
            trout_status[label] = "unreachable"
            return None

    md_gdf = fetch_source("MD", fetch_trout_md)
    va_gdf = fetch_source("VA", fetch_trout_va)
    pa_gdfs = fetch_source("PA", fetch_trout_pa) or {}

    # When MD's live endpoint is down, fall back to the committed COMID seed so
    # MD streams stay tagged (status "bundled-seed" -- a complete source, not a
    # gap, so it satisfies --require-trout). Applied per region below.
    md_seed: tuple[str, set[int]] | None = None
    if md_gdf is None and trout_status.get("MD") == "unreachable":
        md_seed = load_md_seed()
        if md_seed:
            trout_status["MD"] = "bundled-seed"

    # Ordered trout sources -- precedence is MD designated, then VA wild, then
    # the PA layers (first writer wins per COMID, matching the old MD→VA→PA
    # order). Each carries its lon/lat bounds for the per-region bbox prefilter.
    trout_sources: list[tuple[str, gpd.GeoDataFrame, tuple]] = []
    if md_gdf is not None:
        trout_sources.append(("designated", md_gdf, tuple(md_gdf.total_bounds)))
    if va_gdf is not None:
        trout_sources.append(("wild_reproduction", va_gdf,
                              tuple(va_gdf.total_bounds)))
    for cls, g in pa_gdfs.items():
        trout_sources.append((cls, g, tuple(g.total_bounds)))
    # Single-bucket layers (NJ/VT/MA + WV). States don't overlap, so their
    # position in the precedence order vs MD/VA/PA is immaterial.
    for spec in SINGLE_BUCKET_TROUT_LAYERS:
        g = fetch_source(spec["state"], lambda s=spec: fetch_trout_ne(s))
        if g is not None and len(g):
            trout_sources.append((spec["class"], g, tuple(g.total_bounds)))
    # NY is multi-bucket (MGMTCAT -> wild/stocked), like the PA layers.
    ny_gdfs = fetch_source("NY", fetch_trout_ny) or {}
    for cls, g in ny_gdfs.items():
        trout_sources.append((cls, g, tuple(g.total_bounds)))

    seeded = sorted(k for k, v in trout_status.items() if v == "bundled-seed")
    if seeded:
        print(f"\nℹ using bundled seed for: {', '.join(seeded)} "
              f"(live endpoint down; tagging from a prior capture).")
    unreachable = sorted(k for k, v in trout_status.items() if v == "unreachable")
    if unreachable:
        print(f"\n⚠ trout sources unreachable: {', '.join(unreachable)} — "
              f"clickable geometry still builds, but those states are untagged.")
        if args.require_trout:
            print("--require-trout set; refusing to ship an incomplete release.",
                  file=sys.stderr)
            return 3

    # ── Step 2: VPU-streaming build ──
    # One region at a time: download → join trout → emit clickable features →
    # delete its extract. Nothing region-scoped survives to the next iteration
    # except the running output stream + small global counters, so peak memory
    # AND peak disk stay at ~one region regardless of region count.
    #
    # Clickable = (StreamOrder >= 3) ∪ (trout COMIDs, any order). Both are
    # decidable from a single region's own attrs + geometry: order is per-COMID,
    # and a trout join only ever yields COMIDs from the region it ran against
    # (COMIDs partition by VPU). The old global upstream-tributary pass added
    # nothing (its results were order >= 3, already in the base), so there's no
    # cross-region dependency to force a second pass.
    keep = args.keep_extracts or args.skip_download
    print("\n── Building (VPU-streaming) ──")
    resolved: dict[str, dict] = {}   # region id -> {snap, attr} (provenance)
    by_class: dict[str, int] = defaultdict(int)
    val_names = set(PA_WILD_TROUT_VALIDATION)
    name_to_comids: dict[str, set[int]] = defaultdict(set)
    val_clickable: set[int] = set()  # val-name COMIDs that were emitted
    val_trout: set[int] = set()      # val-name COMIDs that are trout
    n_feats = 0
    raw_bytes = len('{"type":"FeatureCollection","features":[]}')

    with gzip.open(OUT_PATH, "wt", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[')
        for region in regions:
            shp, vaa_dbf, archives = prepare_region(
                region, args.cache_dir, args.skip_download)
            if archives:
                resolved[region["id"]] = archives
            attrs = vaa_attrs(vaa_dbf)
            gdf = read_region_gdf(shp)
            id_col = next(c for c in gdf.columns if c.lower() == "comid")
            gnis_col = next((c for c in gdf.columns
                             if c.lower() == "gnis_name"), None)

            # Trout joins for this region only (bbox-gated for speed).
            rbounds = tuple(gdf.total_bounds)
            region_trout: dict[int, str] = {}
            # Bundled MD seed first (highest precedence, == live MD order): tag
            # the seed COMIDs that fall in this region. Membership in `attrs`
            # confines them to their own VPU, mirroring the live spatial join.
            if md_seed:
                seed_cls, seed_comids = md_seed
                for c in seed_comids:
                    if c in attrs:
                        region_trout.setdefault(c, seed_cls)
            for cls, tgdf, tbounds in trout_sources:
                if not _bbox_overlap(rbounds, tbounds):
                    continue
                for c, k in spatial_join_trout(tgdf, gdf, cls, attrs).items():
                    region_trout.setdefault(c, k)
            for cls in region_trout.values():
                by_class[cls] += 1

            seen: set[int] = set()  # dedup within region (COMIDs unique per VPU)
            emitted_here = 0
            for _, row in gdf.iterrows():
                comid = int(row[id_col])
                if comid in seen:
                    continue
                seen.add(comid)
                nm = (str(row[gnis_col]).strip()
                      if gnis_col and row[gnis_col] else None)
                is_val = nm in val_names
                if is_val:
                    name_to_comids[nm].add(comid)
                a = attrs.get(comid)
                order = a.get("streamorder") if a else None
                trout = region_trout.get(comid)
                clickable = (order is not None and order >= MIN_ORDER) \
                    or trout is not None
                if not clickable:
                    continue
                feat = build_feature(comid, row, gnis_col, attrs, trout)
                if not feat:
                    continue
                seg = ("," if n_feats else "") + json.dumps(
                    feat, separators=(",", ":"))
                out.write(seg)
                raw_bytes += len(seg)
                n_feats += 1
                emitted_here += 1
                if is_val:
                    val_clickable.add(comid)
                    if trout is not None:
                        val_trout.add(comid)
            print(f"  [{region['id']}] {emitted_here:,} features"
                  f" ({len(region_trout):,} trout)")

            del gdf, attrs
            if not keep:
                shutil.rmtree(os.path.join(args.cache_dir, region["id"]),
                              ignore_errors=True)
        out.write("]}")

    size = os.path.getsize(OUT_PATH)
    print(f"\n[done] {n_feats:,} flowlines -> {OUT_PATH} "
          f"({size / 1e6:.1f} MB gz, ~{raw_bytes / 1e6:.1f} MB raw)")
    for cls, n in sorted(by_class.items()):
        print(f"  {cls}: {n:,}")

    # ── Step 3: PA validation (data collected during the emit loop) ──
    validate_pa_coverage(val_clickable, val_trout, name_to_comids)

    # ── Step 4: provenance manifest (for the build/ship runbook) ──
    if args.manifest:
        write_manifest(args.manifest, [r["id"] for r in regions], resolved,
                       n_feats, dict(by_class), trout_status)
        print(f"[manifest] wrote {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
