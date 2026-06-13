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
    comid, levelpathid, gnis_name, streamorder, lengthkm,
    trout_class, tier, is_wild, is_native

trout_class is one of:
    wild_reproduction, class_a, wilderness, stocked, designated, null
tier (the nationwide quality axis) is one of: gold, class1, class2, class3, null
is_wild / is_native are booleans (the two filters). See trout_registry +
docs/trout-tier-normalization-rubric.md.

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

import trout_registry  # declarative trout-source registry (data/trout/sources.json)

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

# --- Trout sources ---
# Declarative registry in data/trout/sources.json, loaded + classified by
# trout_registry.py (modes: single / multi_layer / field_map / field_prefix /
# flags). Adding a state is an entry there, not new Python here.

USER_AGENT = "Blueliner/1.0 (+https://blueliner.app)"
REQUEST_TIMEOUT = 20.0
# Per-pull pagination budget. fetch_arcgis_features now RAISES when this (or
# MAX_PAGES) cuts a pull short rather than silently returning a partial layer
# (a truncated capture used to ship -- and worse, could clobber a complete
# seed). Sized generously so a slow-but-alive server finishes: the per-source
# retry + seed fallback handle the genuinely dead ones.
TOTAL_BUDGET = 300.0
MAX_PAGES = 200
MAX_RETRIES = 5  # per-request retries (transport errors + 5xx) before giving up
SPATIAL_JOIN_BUFFER_DEG = 0.001  # ~100 m

# Per-SOURCE retries (on top of _http_get_retry's per-request backoff): a state
# server that passes a light probe but flaps mid-pagination (CPW) gets the whole
# paginated pull retried a bounded number of times before we fall back to its
# seed. 3 attempts, waiting 15s then 45s between them.
SOURCE_FETCH_ATTEMPTS = 3
SOURCE_FETCH_BACKOFF = (15, 45)

# Auto-captured last-known-good seeds: after a successful live fetch+join, each
# source's tagged COMID set is written here (one JSON per source) so the next
# build can tag that state from the prior capture if its server is down.
# data-build.yml commits refreshed seeds back to the repo after a full build.
SEEDS_DIR = os.path.join(ROOT, "data", "nhdplus", "seeds")
PROBE_TIMEOUT = 15.0  # preflight layer-metadata probe

# Preflight patience: a NO-DATA source (no live endpoint AND no seed) is a
# certain --require-trout gate failure, but state GIS servers flap
# independently for minutes at a time (NY tonight, CPW earlier). Instead of
# exiting on the first probe, re-probe just the failing sources every
# PREFLIGHT_REPROBE_INTERVAL seconds for up to --preflight-wait seconds, so
# the seed-bootstrap condition is "each server up at some point in the
# window", not "all ~30 up at the same instant".
PREFLIGHT_REPROBE_INTERVAL = 120.0
PREFLIGHT_WAIT_DEFAULT = 25 * 60.0  # seconds; keeps runner cost sane


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


def prepare_region(region: dict, cache_dir: str,
                   skip_download: bool) -> tuple[str, str, dict]:
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
                   trout_status: dict, tier_counts: dict | None = None,
                   native_count: int = 0) -> None:
    """Provenance sidecar for a built artifact: which regions, which exact NHD
    archive vintages were resolved (so a release is reproducible / auditable),
    the feature count, the trout-class + tier histograms, and which state trout
    sources were reachable (so a degraded build is visible). Answers 'what is
    live?' when shipped alongside the .geojson.gz / .pmtiles."""
    import datetime
    manifest = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                                 .isoformat(timespec="seconds"),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "builder": "build_clickable_streams.py",
        "regions": region_ids,
        "feature_count": feature_count,
        "trout_class_counts": dict(sorted(trout_class_counts.items())),
        "trout_tier_counts": dict(sorted((tier_counts or {}).items())),
        "trout_native_count": native_count,
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
    """Find the OID field name from the layer metadata, checking actual fields.

    Raises on transport/HTTP failure (after _http_get_retry's backoff): a
    transient metadata error must NOT silently downgrade the pull to a single
    unpaginated page -- that used to ship the first 1000 features as if they
    were the whole layer. Returns None only when the metadata is readable but
    exposes no recognizable OID field."""
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
    """Paginate an ArcGIS query endpoint via OBJECTID keyset.

    Raises RuntimeError when the pull is cut short (TOTAL_BUDGET / MAX_PAGES
    exhausted mid-stream, or a full page from a layer that can't be keyset-
    paginated) instead of returning a silently-partial layer: the per-source
    retry wrapper and seed fallback are the right consumers of that failure,
    and a partial capture must never be written out as a last-known-good
    seed."""
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
    complete = False
    truncation = None
    with httpx.Client(timeout=REQUEST_TIMEOUT,
                      headers={"User-Agent": USER_AGENT}) as client:
        oid = _discover_oid_field(client, layer_url, deadline)
        last: int | None = None
        for _ in range(MAX_PAGES):
            if time.monotonic() > deadline:
                truncation = f"time budget ({TOTAL_BUDGET:.0f}s) exhausted"
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
                complete = True
                break
            if not oid:
                # Unpaginatable layer (no OID field): a full first page means
                # there may be more we can't reach -- refuse to guess.
                if len(batch) >= page_size:
                    raise RuntimeError(
                        f"{base}: no OID field and a full first page "
                        f"({len(batch)} features); cannot verify completeness")
                features.extend(batch)
                complete = True
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
                # OID values unusable for keyset paging: same full-page rule.
                if len(batch) >= page_size:
                    raise RuntimeError(
                        f"{base}: unparsable {oid} values on a full page "
                        f"({len(batch)} features); cannot verify completeness")
                features.extend(batch)
                complete = True
                break
            mx = max(ids)
            if last is not None and mx <= last:
                complete = True
                break
            features.extend(batch)
            last = mx
            if len(batch) < page_size:
                complete = True
                break
        else:
            truncation = f"page cap (MAX_PAGES={MAX_PAGES}) exhausted"
    if not complete:
        raise RuntimeError(
            f"truncated fetch from {base}: "
            f"{truncation or 'pagination stopped early'}; "
            f"{len(features)} features pulled before cutoff")
    # Force 2D: some servers ignore returnZ and emit null Z ordinates that
    # break shapely. Safe no-op for already-2D geometry.
    for f in features:
        g = f.get("geometry")
        if g and g.get("coordinates") is not None:
            g["coordinates"] = _strip_z(g["coordinates"])
    return features


# ──────────────────── Last-known-good seeds (per source) ────────────────────
# A seed is a committed capture of one source's tagged COMID set -- everything
# needed to re-tag that state's reaches ((trout_class, tier, native) per group)
# without its live endpoint. Two flavors:
#   * auto-captured: data/nhdplus/seeds/<slug>.json, written by this build after
#     every successful live fetch+join (full builds only) and committed back by
#     data-build.yml. Preferred because it's the freshest.
#   * legacy `seed:` registry key (MD): a hand-captured single-class file; kept
#     working as a pre-seeded entry until the auto seed supersedes it.

def seed_slug(source: dict) -> str:
    """Stable filesystem-safe slug of state+label (one seed file per source;
    `label` disambiguates states with multiple sources, e.g. CT)."""
    raw = f"{source['state']} {source.get('label', '')}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")


def seed_path(source: dict, seed_dir: str = SEEDS_DIR) -> str:
    return os.path.join(seed_dir, f"{seed_slug(source)}.json")


def _parse_seed_groups(seed: dict, source: dict) -> list[tuple[tuple, set[int]]]:
    """Normalize a seed file (either flavor) to [((class, tier, native),
    {COMIDs}), ...]. Legacy single-class files get the class-fallback tier and
    the source's registry `native` flag, matching the old MD behavior."""
    if "groups" in seed:  # auto-captured shape
        out = []
        for g in seed["groups"]:
            comids = {int(c) for c in g.get("comids", [])}
            if comids:
                out.append(((g["trout_class"], g.get("tier"),
                             bool(g.get("native", False))), comids))
        return out
    comids = {int(c) for c in seed.get("comids", [])}  # legacy shape
    if not comids:
        return []
    cls = seed["trout_class"]
    return [((cls, trout_registry.FALLBACK_CLASS_TIER.get(cls),
              trout_registry.is_native(source)), comids)]


def find_seed_file(source: dict, seed_dir: str = SEEDS_DIR) -> str | None:
    """Path of this source's seed if one exists (auto capture preferred, then
    the legacy `seed:` registry file). None when the source has no seed yet --
    normal until the first post-merge full build populates the directory."""
    auto = seed_path(source, seed_dir)
    if os.path.exists(auto):
        return auto
    legacy = source.get("seed")
    if legacy:
        path = os.path.join(ROOT, legacy)
        if os.path.exists(path):
            return path
    return None


def load_source_seed(source: dict,
                     seed_dir: str = SEEDS_DIR) -> list[tuple[tuple, set[int]]]:
    """Fallback when a live endpoint is down: load this source's committed
    capture. Returns [] when no usable seed exists."""
    path = find_seed_file(source, seed_dir)
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            seed = json.load(f)
        groups = _parse_seed_groups(seed, source)
    except Exception as e:
        print(f"  WARNING: unusable seed {path}: {e}")
        return []
    if not groups:
        return []
    n = sum(len(c) for _, c in groups)
    captured = seed.get("captured_at") or seed.get("captured_from") or "unknown date"
    stale = ""
    try:
        import datetime
        dt = datetime.datetime.fromisoformat(str(seed["captured_at"]))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        age = (datetime.datetime.now(datetime.timezone.utc) - dt).days
        stale = f"; {age}d stale"
    except Exception:
        pass
    print(f"  [trout] seed: tagging {n:,} COMIDs from a prior capture "
          f"({os.path.relpath(path, ROOT)}, captured {captured}{stale})")
    return groups


def write_source_seed(source: dict, groups: dict[tuple, set[int]],
                      seed_dir: str = SEEDS_DIR) -> str | None:
    """Persist a source's freshly-captured tagged COMID set (compact JSON,
    sorted int lists). Skips the write -- preserving captured_at, so the
    workflow's changed-seeds guard stays meaningful -- when the capture is
    empty or identical to the existing seed. Returns the path written."""
    glist = []
    for key in sorted(groups, key=lambda k: (k[0] or "", k[1] or "", k[2])):
        comids = sorted(int(c) for c in groups[key])
        if not comids:
            continue
        cls, tier, native = key
        glist.append({"trout_class": cls, "tier": tier, "native": bool(native),
                      "comid_count": len(comids), "comids": comids})
    if not glist:
        return None
    path = seed_path(source, seed_dir)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                prev = json.load(f).get("groups")
            if prev is not None and [
                {k: g[k] for k in ("trout_class", "tier", "native", "comids")}
                for g in prev
            ] == [
                {k: g[k] for k in ("trout_class", "tier", "native", "comids")}
                for g in glist
            ]:
                return None  # unchanged; keep the existing captured_at
        except Exception:
            pass  # unreadable previous seed -> overwrite
    import datetime
    obj = {
        "version": 1,
        "state": source["state"],
        "label": source.get("label", source["state"]),
        "captured_at": datetime.datetime.now(datetime.timezone.utc)
                               .isoformat(timespec="seconds"),
        "git_sha": os.environ.get("GITHUB_SHA"),
        "comid_count": sum(g["comid_count"] for g in glist),
        "groups": glist,
    }
    os.makedirs(seed_dir, exist_ok=True)
    # Atomic replace: a crash mid-write must not leave a half-written seed --
    # next run would log "unusable seed" and the source would silently regress
    # to NO DATA (the exact gate failure seeds exist to prevent).
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
        f.write("\n")
    os.replace(tmp, path)
    return path


# ──────────────────────── Trout GIS ingestion ────────────────────────


def _split_by_value(gdf: gpd.GeoDataFrame,
                    source: dict) -> list[tuple[tuple, gpd.GeoDataFrame]]:
    """Tag each feature with its (trout_class, tier, native) via the registry,
    then group into [((cls, tier, native), gdf), ...]. Rows the registry maps to
    no class are dropped -- matching the old per-state map/groupby behaviour."""
    native = trout_registry.is_native(source)
    groups: dict[tuple, list] = {}
    for idx, row in gdf.iterrows():
        rd = dict(row)
        cls = trout_registry.row_bucket(source, rd)
        if not cls:
            continue
        key = (cls, trout_registry.row_tier(source, rd), native)
        groups.setdefault(key, []).append(idx)
    out = []
    for key, idxs in groups.items():
        out.append((key, gdf.loc[idxs]))
        print(f"  {key}: {len(idxs)} features")
    return out


def fetch_trout_source(source: dict) -> list[tuple[tuple, gpd.GeoDataFrame]]:
    """Fetch one registry source -> [((trout_class, tier, native), GeoDataFrame),
    ...], handling every mode. Raises on transport error so collect_trout_taggers
    can retry and then fall back to the source's last-known-good seed."""
    if source["mode"] == "multi_layer":
        out: list[tuple[tuple, gpd.GeoDataFrame]] = []
        for layer in source["layers"]:
            url = f"{source['base']}/{layer['id']}/query?where=1%3D1"
            print(f"[trout] {source['state']} {layer['label']} "
                  f"(layer {layer['id']}) ...")
            feats = fetch_arcgis_features(url)
            if not feats:
                print(f"  WARNING: no features for layer {layer['id']}")
                continue
            gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
            print(f"  {len(gdf)} features")
            key = (layer["class"], trout_registry.layer_tier(layer),
                   trout_registry.is_native(source, layer))
            out.append((key, gdf))
        return out

    print(f"[trout] {source['label']} ...")
    feats = fetch_arcgis_features(source["url"])
    if not feats:
        print("  WARNING: no features returned")
        return []
    gdf = gpd.GeoDataFrame.from_features(feats, crs="EPSG:4326")
    print(f"  {len(gdf)} features")

    if source["mode"] == "single":
        key = (source["class"], trout_registry.row_tier(source, {}),
               trout_registry.is_native(source))
        return [(key, gdf)]

    needed = trout_registry.classify_fields(source)
    if needed and not any(f in gdf.columns for f in needed):
        print(f"  WARNING: classify field(s) {needed} absent; "
              f"skipping {source['state']}")
        return []
    return _split_by_value(gdf, source)


def fetch_trout_source_with_retries(source: dict,
                                    attempts: int = SOURCE_FETCH_ATTEMPTS,
                                    backoff=SOURCE_FETCH_BACKOFF,
                                    fetch=None, sleep=time.sleep):
    """Per-SOURCE retry wrapper around fetch_trout_source: a server that dies
    mid-pagination (one bad page) gets the whole source re-pulled after a
    15s/45s wait, a bounded number of times -- not per-page-infinitely. Raises
    the last error after `attempts` so the caller can fall back to a seed."""
    fetch = fetch or fetch_trout_source
    err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fetch(source)
        except Exception as e:
            err = e
            if attempt >= attempts:
                break
            wait = backoff[min(attempt - 1, len(backoff) - 1)]
            print(f"  source retry {attempt}/{attempts - 1} after {e} "
                  f"(waiting {wait}s)")
            sleep(wait)
    raise err  # type: ignore[misc]


def collect_trout_taggers(sources: list[dict], fetch=None,
                          seed_dir: str = SEEDS_DIR,
                          fetch_last: set | None = None):
    """Fetch every registry source (with per-source retries), falling back to
    its last-known-good seed when the live endpoint is down.

    `fetch_last` (labels): sources the preflight saw flapping are fetched
    AFTER the healthy ones, buying them maximal extra recovery time. The
    returned tagger list is ALWAYS in registry order (= precedence) no matter
    the fetch order, so reordering never changes the output.

    A fetch failure on a source with NO seed isn't final immediately: that
    source gets one more full fetch attempt at the very end of the phase
    (after every other source has been pulled), and only then becomes
    "unreachable" -- a flapping server gets the whole fetch phase to recover
    before it can trip the --require-trout gate.

    Returns (taggers, trout_status). Each tagger is either
        {"slug", "label", "source", "live": True,
         "groups": [((class, tier, native), GeoDataFrame, bounds), ...]}
    or, seed-backed,
        {"slug", "label", "source", "live": False,
         "groups": [((class, tier, native), {COMIDs}), ...]}.
    trout_status[label] is "ok" | "bundled-seed" | "unreachable"; "unreachable"
    means NEITHER live data NOR a seed (the --require-trout gate)."""
    fetch = fetch or fetch_trout_source_with_retries
    results: dict[int, dict] = {}  # registry index -> tagger
    trout_status: dict[str, str] = {}

    def _live_tagger(source, label, result):
        groups = [(key, g, tuple(g.total_bounds))
                  for key, g in result if len(g)]
        return {"slug": seed_slug(source), "label": label,
                "source": source, "live": True, "groups": groups}

    indexed = list(enumerate(sources))
    if fetch_last:
        # Stable partition: healthy sources first (registry order preserved
        # within each partition), preflight-shaky ones last.
        indexed.sort(key=lambda p: p[1].get("label", p[1]["state"]) in fetch_last)

    no_seed_failures: list[tuple[int, dict, str]] = []
    for idx, source in indexed:
        # `label` lets one state contribute multiple sources (e.g. CT's WTMA
        # wild layer + its general stocked-streams layer), each tracked
        # independently in trout_status; defaults to the state code.
        label = source.get("label", source["state"])
        result = None
        try:
            result = fetch(source)
            trout_status[label] = "ok"
        except Exception as e:  # transport/HTTP error after all retries
            print(f"  WARNING: {label} trout source unreachable: {e}")
        if result is not None:
            results[idx] = _live_tagger(source, label, result)
            continue
        seed_groups = load_source_seed(source, seed_dir)
        if seed_groups:
            results[idx] = {"slug": seed_slug(source), "label": label,
                            "source": source, "live": False,
                            "groups": seed_groups}
            trout_status[label] = "bundled-seed"
        else:
            no_seed_failures.append((idx, source, label))

    # Final verdict pass: a failed source WITH a seed already degraded
    # gracefully above; a failed source WITHOUT one would fail the gate, so
    # give each exactly one more full fetch attempt now that the rest of the
    # phase has elapsed.
    for idx, source, label in no_seed_failures:
        print(f"  [final-retry] {label}: fetch failed and no seed exists; "
              f"one last attempt before the verdict ...")
        try:
            result = fetch(source)
        except Exception as e:
            print(f"  WARNING: {label} still unreachable on final retry: {e}")
            trout_status[label] = "unreachable"
            continue
        print(f"  [final-retry] {label}: recovered")
        trout_status[label] = "ok"
        results[idx] = _live_tagger(source, label, result)

    taggers = [results[i] for i in sorted(results)]  # back to registry order
    return taggers, trout_status


# ──────────────────────── Preflight reachability ────────────────────────

def source_probe_urls(source: dict) -> list[str]:
    """Layer-metadata URL(s) to probe for one source (cheap GET, no features)."""
    if source["mode"] == "multi_layer":
        return [f"{source['base']}/{layer['id']}" for layer in source["layers"]]
    from urllib.parse import urlsplit
    s = urlsplit(source["url"])
    return [f"{s.scheme}://{s.netloc}{s.path}".rsplit("/query", 1)[0]]


def _probe_layer(url: str) -> bool:
    """True if the layer's metadata endpoint answers sanely. ArcGIS returns
    HTTP 200 with an `error` body for broken layers, so check both."""
    try:
        with httpx.Client(timeout=PROBE_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": USER_AGENT}) as client:
            r = client.get(url, params={"f": "json"})
            return r.status_code == 200 and "error" not in r.json()
    except Exception:
        return False


def preflight_sources(sources: list[dict], seed_dir: str = SEEDS_DIR,
                      probe=None) -> list[tuple[str, str]]:
    """Quick reachability pass over all sources BEFORE any heavy work:
    [(label, "reachable" | "will-use-seed" | "NO DATA"), ...]. A probe pass is
    no guarantee the full paginated pull succeeds (CPW flaps exactly this way)
    -- the fetch stage still has its own seed fallback -- but a NO-DATA source
    (no live endpoint, no seed) is certain to fail the gate, so the build can
    bail before the NHDPlus downloads (after the bounded re-probe window of
    preflight_wait_for_no_data)."""
    probe = probe or (lambda s: all(_probe_layer(u) for u in source_probe_urls(s)))
    rows: list[tuple[str, str]] = []
    for source in sources:
        label = source.get("label", source["state"])
        if probe(source):
            status = "reachable"
        elif find_seed_file(source, seed_dir):
            status = "will-use-seed"
        else:
            status = "NO DATA"
        rows.append((label, status))
    return rows


def preflight_wait_for_no_data(no_data_sources: list[dict], wait_budget: float,
                               interval: float = PREFLIGHT_REPROBE_INTERVAL,
                               probe=None, sleep=time.sleep,
                               clock=time.monotonic):
    """Bounded recovery window for preflight NO-DATA sources: re-probe just
    the failing ones every `interval` seconds until they all answer or
    `wait_budget` seconds have elapsed (the final round is clipped so the
    full budget is used). Servers flap independently for minutes at a time,
    so until seeds exist this turns the bootstrap condition "every server up
    at the same instant" into "each server up at some point in the window".

    Returns (recovered_labels, still_no_data_labels). `probe`/`sleep`/`clock`
    are injectable for tests."""
    probe = probe or (lambda s: all(_probe_layer(u) for u in source_probe_urls(s)))
    pending = list(no_data_sources)
    recovered: list[str] = []
    start = clock()
    rnd = 0
    while pending:
        wait = min(interval, wait_budget - (clock() - start))
        if wait <= 0:
            break
        rnd += 1
        names = ", ".join(s.get("label", s["state"]) for s in pending)
        print(f"  [preflight-wait] round {rnd}: sleeping {wait:.0f}s, then "
              f"re-probing {len(pending)} source(s): {names}")
        sleep(wait)
        still: list[dict] = []
        for source in pending:
            label = source.get("label", source["state"])
            if probe(source):
                print(f"  [preflight-wait] {label} recovered "
                      f"(after {clock() - start:.0f}s)")
                recovered.append(label)
            else:
                still.append(source)
        pending = still
    still_labels = [s.get("label", s["state"]) for s in pending]
    if still_labels:
        print(f"  [preflight-wait] budget exhausted ({wait_budget:.0f}s); "
              f"still NO DATA: {', '.join(still_labels)}")
    return recovered, still_labels


# ──────────────────────── Join trout to NHD COMIDs ────────────────────────

def spatial_join_trout(trout_gdf: gpd.GeoDataFrame,
                       nhd_gdf: gpd.GeoDataFrame,
                       all_attrs: dict[int, dict]) -> set:
    """Spatial join: buffer trout lines and return the set of overlapping NHD
    COMIDs (confined to this region via `all_attrs` membership). The caller
    applies the source's (class, tier) and native flag."""
    import warnings
    id_col = next(c for c in nhd_gdf.columns if c.lower() == "comid")
    # Rename the NHD id column to a collision-proof sentinel BEFORE the join.
    # A trout layer can carry its own id field whose name case-exactly matches
    # this region's NHD column (e.g. NHDPlus-catchment overlays like the EBTJV
    # portfolio carry a "ComID" field, and some VPU shapefiles spell the NHD
    # column "ComID" rather than "COMID"). gpd.sjoin suffixes such colliding
    # columns to <name>_left/<name>_right, after which `joined[id_col]` is a
    # KeyError. Renaming up front removes the collision regardless of casing.
    nhd_sub = nhd_gdf[[id_col, "geometry"]].copy()
    nhd_sub = nhd_sub.rename(columns={id_col: "_nhd_comid"})
    nhd_sub = nhd_sub[~nhd_sub.geometry.isna() & ~nhd_sub.geometry.is_empty]

    trout_buf = trout_gdf.copy()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        trout_buf["geometry"] = trout_buf.geometry.buffer(SPATIAL_JOIN_BUFFER_DEG)
    trout_buf = trout_buf[~trout_buf.geometry.isna() & ~trout_buf.geometry.is_empty]
    # Drop any column that would still collide with our sentinel (paranoia: a
    # source field literally named "_nhd_comid"); the trout attrs aren't needed.
    trout_buf = trout_buf[["geometry"]]
    if nhd_sub.empty or trout_buf.empty:
        return set()

    joined = gpd.sjoin(nhd_sub, trout_buf, how="inner", predicate="intersects")
    if joined.empty:  # bbox-overlapping but no actual intersection
        return set()
    comids = set(int(c) for c in joined["_nhd_comid"].unique() if c is not None)
    return {c for c in comids if c in all_attrs}


def _bbox_overlap(a, b) -> bool:
    """True if two (minx, miny, maxx, maxy) bounds intersect. Used to skip
    spatial joins between a state's trout layer and regions it can't touch --
    each state overlaps only 1-2 of the ~18 NHDPlus regions, so this is what
    keeps per-state joins from scaling with national geometry."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


# ──────────────────────── Feature assembly ────────────────────────

def build_feature(comid: int, row, gnis_col: str | None,
                  attrs: dict, trout: tuple | None, native: bool = False) -> dict | None:
    """Build a single GeoJSON feature dict. `trout` is the (trout_class, tier)
    for this COMID (or None for an order-only reach); `native` is the OR-merged
    native-overlay flag (independent of which source won the class/tier)."""
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
    order = int(a["streamorder"]) if a["streamorder"] else None
    cls, tier = trout if trout else (None, None)
    is_wild = trout_registry.class_is_wild(cls) if cls else False
    # Size ladder: generic wild on a named river (order>=3) -> class1; designated
    # premier-wild on a named river (order>=4) -> gold. See trout_registry.
    tier = trout_registry.refine_tier(tier, is_wild, name, order)
    return {
        "type": "Feature",
        "geometry": shapely.geometry.mapping(geom),
        "properties": {
            "comid": comid,
            "levelpathid": a["levelpathid"],
            "gnis_name": name,
            "streamorder": order,
            "lengthkm": a["lengthkm"],
            "trout_class": cls,
            "tier": tier,
            "is_wild": is_wild,
            "is_native": bool(native),
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
                   help="Fail (exit 3) if any state trout source has NEITHER "
                        "live data NOR a last-known-good seed, instead of "
                        "degrading to untagged geometry. Use for canonical/"
                        "published release builds.")
    p.add_argument("--preflight-wait", type=float,
                   default=PREFLIGHT_WAIT_DEFAULT, metavar="SECONDS",
                   help="With --require-trout: when preflight finds NO-DATA "
                        "sources (no live endpoint, no seed), re-probe just "
                        "those every ~2 min for up to this many seconds before "
                        "exiting 3 -- state GIS servers flap independently for "
                        "minutes at a time. 0 disables the wait. "
                        f"Default: {PREFLIGHT_WAIT_DEFAULT:.0f}s.")
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

    sources = trout_registry.load_sources()

    # ── Step 0: Preflight (fail-before-heavy-work, with bounded patience) ──
    # Cheap layer-metadata probes over all sources. Only a NO-DATA source (no
    # live endpoint AND no seed) is a certain gate failure; before treating
    # that as final, re-probe just the failing sources inside a bounded wait
    # window (--preflight-wait) -- servers flap independently for minutes, so
    # an instant exit 3 makes seed bootstrap nearly impossible. A flaky-but-
    # probe-passing server still gets the full fetch + seed-fallback
    # treatment below.
    print("── Trout-source preflight ──")
    pf_rows = preflight_sources(sources)
    width = max(len(label) for label, _ in pf_rows)
    for label, status in pf_rows:
        print(f"  {label:<{width}}  {status}")
    no_data = [label for label, status in pf_rows if status == "NO DATA"]
    if no_data:
        print(f"\n⚠ preflight: no live endpoint AND no seed for: "
              f"{', '.join(no_data)}")
        if args.require_trout and args.preflight_wait > 0:
            print(f"  --require-trout: re-probing every "
                  f"{PREFLIGHT_REPROBE_INTERVAL:.0f}s for up to "
                  f"{args.preflight_wait:.0f}s before giving up ...")
            by_label = {s.get("label", s["state"]): s for s in sources}
            recovered, no_data = preflight_wait_for_no_data(
                [by_label[l] for l in no_data], args.preflight_wait)
            if recovered:
                print(f"  recovered during preflight wait: "
                      f"{', '.join(recovered)}")
        if no_data and args.require_trout:
            print("--require-trout set; failing before NHDPlus downloads "
                  f"(still NO DATA: {', '.join(no_data)}).", file=sys.stderr)
            return 3

    # ── Step 1: Fetch state trout GIS (small; held in memory once) ──
    # Third-party state ArcGIS servers occasionally go down. Each source gets
    # per-request backoff (_http_get_retry) plus per-source retries
    # (fetch_trout_source_with_retries); after those, it falls back to its
    # last-known-good seed (data/nhdplus/seeds/, or the legacy `seed:` registry
    # key). Only a source with NEITHER live data NOR a seed is "unreachable" --
    # the clickable geometry still ships, that state just isn't trout-tagged.
    # A canonical release should pass --require-trout so an incomplete artifact
    # never gets published silently.
    #
    # Tagger order = registry order = precedence (first writer wins per COMID);
    # states don't overlap, so order among them is immaterial. FETCH order is
    # different: sources the preflight saw flapping go last (max recovery
    # time); collect_trout_taggers restores registry order in its output.
    print("\n── Trout-source fetch ──")
    shaky = {label for label, status in pf_rows if status != "reachable"}
    if shaky:
        print(f"  (fetching preflight-shaky sources last: "
              f"{', '.join(sorted(shaky))})")
    taggers, trout_status = collect_trout_taggers(sources, fetch_last=shaky)

    seeded = sorted(k for k, v in trout_status.items() if v == "bundled-seed")
    if seeded:
        print(f"\nℹ using bundled seed for: {', '.join(seeded)} "
              f"(live endpoint down; tagging from a prior capture).")
    unreachable = sorted(k for k, v in trout_status.items() if v == "unreachable")
    if unreachable:
        print(f"\n⚠ trout sources unreachable (no live data, no seed): "
              f"{', '.join(unreachable)} — clickable geometry still builds, "
              f"but those states are untagged.")
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
    # Per-source live-join capture: slug -> key -> COMIDs, written out as
    # last-known-good seeds after the loop (full builds only).
    captured: dict[str, dict[tuple, set[int]]] = \
        defaultdict(lambda: defaultdict(set))
    by_class: dict[str, int] = defaultdict(int)  # emitted reaches per class
    by_tier: dict[str, int] = defaultdict(int)   # post eastern-gold (calibration)
    native_count = 0                             # emitted reaches with is_native
    val_names = set(PA_WILD_TROUT_VALIDATION)
    name_to_comids: dict[str, set[int]] = defaultdict(set)
    val_clickable: set[int] = set()  # val-name COMIDs that were emitted
    val_trout: set[int] = set()      # val-name COMIDs that are trout
    n_feats = 0
    raw_bytes = len('{"type":"FeatureCollection","features":[]}')

    # Write to a sibling temp file and os.replace at the end: a crash mid-build
    # must not leave a truncated-but-valid-looking .geojson.gz where a previous
    # good artifact used to be.
    tmp_out = OUT_PATH + ".tmp"
    with gzip.open(tmp_out, "wt", encoding="utf-8") as out:
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
            region_trout: dict[int, tuple] = {}   # comid -> (class, tier); first wins
            region_native: set[int] = set()        # comid -> native; OR-merged overlay
            # class/tier are first-writer-wins (state precedence = tagger order);
            # the native flag is OR-merged across sources so a native overlay
            # (e.g. TU brook trout) enriches a state-claimed reach instead of
            # being dropped on collision. Seed-backed taggers replay a prior
            # capture: membership in `attrs` confines their COMIDs to this VPU,
            # mirroring the live spatial join. Live join results accumulate into
            # `captured` so a fresh seed can be written after the build.
            for tagger in taggers:
                for grp in tagger["groups"]:
                    if tagger["live"]:
                        key, tgdf, tbounds = grp
                        if not _bbox_overlap(rbounds, tbounds):
                            continue
                        hits = spatial_join_trout(tgdf, gdf, attrs)
                        captured[tagger["slug"]][key] |= hits
                    else:
                        key, seed_comids = grp
                        hits = {c for c in seed_comids if c in attrs}
                    cls, tier, nat = key
                    for c in hits:
                        region_trout.setdefault(c, (cls, tier))
                        if nat:
                            region_native.add(c)

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
                native = comid in region_native
                clickable = (order is not None and order >= MIN_ORDER) \
                    or trout is not None or native
                if not clickable:
                    continue
                feat = build_feature(comid, row, gnis_col, attrs, trout, native)
                if not feat:
                    continue
                # Count classes/tiers from EMITTED features (not raw join
                # hits) so the manifest's class and tier histograms describe
                # the same population and stay mutually consistent.
                ftier = feat["properties"]["tier"]
                if ftier:
                    by_tier[ftier] += 1
                fcls = feat["properties"]["trout_class"]
                if fcls:
                    by_class[fcls] += 1
                if feat["properties"].get("is_native"):
                    native_count += 1
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
    os.replace(tmp_out, OUT_PATH)

    size = os.path.getsize(OUT_PATH)
    print(f"\n[done] {n_feats:,} flowlines -> {OUT_PATH} "
          f"({size / 1e6:.1f} MB gz, ~{raw_bytes / 1e6:.1f} MB raw)")
    for cls, n in sorted(by_class.items()):
        print(f"  {cls}: {n:,}")
    print("  tiers (post eastern-gold):")
    _tier_total = sum(by_tier.values()) or 1
    for t in ("gold", "class1", "class2", "class3"):
        n = by_tier.get(t, 0)
        print(f"    {t}: {n:,} ({100 * n / _tier_total:.1f}% of tagged)")
    print(f"  native reaches (is_native): {native_count:,}")

    # ── Step 3: refresh last-known-good seeds (full builds only) ──
    # A partial --regions run would capture only a subset of a source's COMIDs
    # and clobber a complete national seed, so skip it there. data-build.yml
    # commits any changed seed files back to the repo after the build.
    if args.regions.strip():
        print("\n[seeds] partial-region build; not refreshing seed captures")
    else:
        wrote = 0
        for tagger in taggers:
            if not tagger["live"] or trout_status.get(tagger["label"]) != "ok":
                continue
            path = write_source_seed(tagger["source"], captured[tagger["slug"]])
            if path:
                n = sum(len(c) for c in captured[tagger["slug"]].values())
                print(f"[seeds] {os.path.relpath(path, ROOT)} ({n:,} COMIDs)")
                wrote += 1
        print(f"[seeds] refreshed {wrote} seed file(s) in "
              f"{os.path.relpath(SEEDS_DIR, ROOT)}")

    # ── Step 4: PA validation (data collected during the emit loop) ──
    validate_pa_coverage(val_clickable, val_trout, name_to_comids)

    # ── Step 5: provenance manifest (for the build/ship runbook) ──
    if args.manifest:
        write_manifest(args.manifest, [r["id"] for r in regions], resolved,
                       n_feats, dict(by_class), trout_status, dict(by_tier),
                       native_count)
        print(f"[manifest] wrote {args.manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
