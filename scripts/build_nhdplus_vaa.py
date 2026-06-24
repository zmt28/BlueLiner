#!/usr/bin/env python3
"""
One-time prep: build data/nhdplus/vaa.csv.gz from NHDPlusV2 archives.

Per region, downloads two 7z components, extracts only the DBFs we
need, joins them on ComID, and appends to a single gzipped CSV:

  - NHDPlusAttributes / PlusFlowlineVAA.dbf
      -> Hydroseq, LevelPathID, StreamLevel, LengthKM (the routing
         attributes that drive same-river filtering)
  - NHDPlusAttributes / elevslope.dbf
      -> MAXELEVSMO, MINELEVSMO (smoothed reach-end elevations, cm),
         which drive the stream elevation/gradient profile
  - NHDSnapshot / NHDFlowline.dbf
      -> GNIS_Name (NHD's authoritative name for the reach; lives in
         the geometry-table component, not VAA)

Output is committed to the repo. Runtime loads it once into a
Postgres table on first boot and never touches EPA. Re-run only when
expanding regions.

Coverage: by default the script DISCOVERS every CONUS Vector Processing
Unit (VPU 01-18) by listing the EPA NHDPlusV21 S3 bucket and picking the
latest vintage of each component archive -- so a national build needs no
hand-maintained URL list (the per-archive `_<nn>` vintage suffixes are
not uniform across regions, which made the old hardcoded list brittle).
Restrict with --vpu MA_02,MS_05 for a fast regional build; AK/HI/PR
(VPU 19/20/21) are out of scope.

Dev dependencies (NOT in runtime requirements.txt):
    pip install dbfread py7zr httpx

Run from repo root:
    python scripts/build_nhdplus_vaa.py                 # national (CONUS)
    python scripts/build_nhdplus_vaa.py --vpu MA_02,MS_05   # regional
    python scripts/build_nhdplus_vaa.py --list          # list discovered VPUs
"""

from __future__ import annotations

import argparse
import csv
import gzip
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

# py7zr / dbfread are dev-only (NOT in runtime requirements.txt); httpx is
# in requirements but only needed for the networked steps. All three are
# imported lazily inside the functions that use them so the pure helpers
# (parse_archive_key / select_latest_archives / _clean_elev / ...) import
# -- and unit-test -- without the dev deps installed.

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "nhdplus", "vaa.csv.gz")
BUCKET_ROOT = "https://dmap-data-commons-ow.s3.amazonaws.com"
S3_BASE = f"{BUCKET_ROOT}/NHDPlusV21/Data"
LIST_PREFIX = "NHDPlusV21/Data/"

# NHDPlusV2 archive filename, e.g.
#   NHDPlusV21_MA_02_NHDPlusAttributes_09.7z
#   NHDPlusV21_SA_03N_NHDSnapshot_07.7z
_ARCHIVE_RE = re.compile(
    r"NHDPlusV21_(?P<da>[A-Z]{2})_(?P<vpu>\d{2}[A-Za-z]?)_"
    r"(?P<comp>NHDPlusAttributes|NHDSnapshot)_(?P<vintage>\d+)\.7z$")

# ELEVSMO sentinel for "no elevation computed" (NHDPlus uses -9998).
ELEV_NODATA = -9998

OUT_COLUMNS = ["comid", "hydroseq", "levelpathid", "streamlevel",
               "gnis_name", "lengthkm", "maxelevsmo", "minelevsmo"]


# -- region discovery (pure helpers + the one networked lister) --------

def parse_archive_key(key: str) -> dict | None:
    """Parse an S3 key for an NHDPlusV2 component archive into
    {da, vpu_id, comp, vintage, url}, or None if it isn't one. Pure."""
    m = _ARCHIVE_RE.search(key)
    if not m:
        return None
    da, vpu = m.group("da"), m.group("vpu")
    return {
        "da": da,
        "vpu_id": f"{da}_{vpu}",          # e.g. "MA_02", "SA_03N"
        "comp": m.group("comp"),
        "vintage": int(m.group("vintage")),
        "url": f"{BUCKET_ROOT}/{key}",
    }


def vpu_in_conus(vpu_id: str) -> bool:
    """True for lower-48 VPUs (numeric 01-18). AK/HI/PR/PI (19-22) and
    anything non-numeric are excluded. Pure."""
    m = re.search(r"(\d{2})", vpu_id)
    return bool(m) and 1 <= int(m.group(1)) <= 18


def select_latest_archives(keys) -> dict:
    """From an iterable of S3 keys, group component archives by VPU and
    keep only the latest vintage of each component. Returns
    {vpu_id: {"vaa": url, "snap": url}} for VPUs that have BOTH the
    Attributes and Snapshot archives. CONUS-only. Pure (no network)."""
    # vpu_id -> comp -> (vintage, url) of the best seen so far
    best: dict[str, dict[str, tuple[int, str]]] = {}
    for key in keys:
        info = parse_archive_key(key)
        if not info or not vpu_in_conus(info["vpu_id"]):
            continue
        slot = best.setdefault(info["vpu_id"], {})
        prev = slot.get(info["comp"])
        if prev is None or info["vintage"] > prev[0]:
            slot[info["comp"]] = (info["vintage"], info["url"])
    out: dict[str, dict[str, str]] = {}
    for vpu_id, comps in sorted(best.items()):
        if "NHDPlusAttributes" in comps and "NHDSnapshot" in comps:
            out[vpu_id] = {
                "vaa": comps["NHDPlusAttributes"][1],
                "snap": comps["NHDSnapshot"][1],
            }
    return out


def list_bucket_keys(client) -> list[str]:
    """List every object key under the NHDPlusV21 data prefix (paginated
    S3 ListObjectsV2 XML). Networked -- runs only in the data-build env."""
    keys: list[str] = []
    token = None
    ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
    while True:
        params = {"list-type": "2", "prefix": LIST_PREFIX, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = client.get(f"{BUCKET_ROOT}/", params=params, timeout=60.0)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        for c in root.findall(f"{ns}Contents"):
            k = c.findtext(f"{ns}Key")
            if k:
                keys.append(k)
        if root.findtext(f"{ns}IsTruncated") == "true":
            token = root.findtext(f"{ns}NextContinuationToken")
            if not token:
                break
        else:
            break
    return keys


def discover_regions(only: set[str] | None = None) -> list[dict]:
    """Discover the CONUS VPU archive set from S3. `only` restricts to a
    set of vpu_ids (e.g. {"MA_02"}). Returns sorted region dicts."""
    import httpx
    with httpx.Client(follow_redirects=True) as c:
        keys = list_bucket_keys(c)
    chosen = select_latest_archives(keys)
    regions = []
    for vpu_id, urls in chosen.items():
        if only and vpu_id not in only:
            continue
        regions.append({"id": vpu_id, "label": vpu_id,
                        "vaa": urls["vaa"], "snap": urls["snap"]})
    return regions


def download(url: str, dest: str) -> None:
    import httpx
    sys.stdout.write(f"  fetching {os.path.basename(url)} ... ")
    sys.stdout.flush()
    with httpx.stream("GET", url, timeout=120.0,
                      follow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    print(f"{os.path.getsize(dest) / 1e6:.1f} MB")


def extract_dbf(archive: str, workdir: str, name: str) -> str:
    """Extract only `<name>.dbf` (not the heavy SHP) from a 7z archive.
    Case-insensitive on the stem (elevslope.dbf is lowercased in some
    vintages, ELEVSLOPE.dbf in others)."""
    import py7zr
    with py7zr.SevenZipFile(archive, mode="r") as z:
        low = f"{name}.dbf".lower()
        targets = [n for n in z.getnames() if n.lower().endswith(low)]
        if not targets:
            raise RuntimeError(
                f"{name}.dbf not in {os.path.basename(archive)}; "
                f"first entries: {z.getnames()[:5]}")
        z.extract(path=workdir, targets=targets)
        return os.path.join(workdir, targets[0])


def gnis_names_from_flowline(dbf_path: str) -> dict[int, str]:
    """COMID -> GNIS_Name (empties dropped)."""
    from dbfread import DBF
    out: dict[int, str] = {}
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("COMID")
        if comid is None:
            continue
        name = rec.get("GNIS_NAME")
        if name:
            out[int(comid)] = str(name).strip()
    return out


def _clean_elev(v) -> int | None:
    """Smoothed reach-end elevation (cm) -> int, dropping the NHDPlus
    NODATA sentinel and anything non-numeric. Pure."""
    if v is None:
        return None
    try:
        e = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return None if e <= ELEV_NODATA else e


def elev_from_elevslope(dbf_path: str) -> dict[int, tuple]:
    """COMID -> (maxelevsmo, minelevsmo) in cm, from elevslope.dbf.
    Either value may be None when NHDPlus has no smoothed elevation."""
    from dbfread import DBF
    out: dict[int, tuple] = {}
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("COMID")
        if comid is None:
            continue
        out[int(comid)] = (_clean_elev(rec.get("MAXELEVSMO")),
                           _clean_elev(rec.get("MINELEVSMO")))
    return out


def vaa_rows(dbf_path: str, gnis: dict[int, str], elev: dict[int, tuple]):
    """Yield projected VAA dicts joined with GNIS_Name from the snapshot
    and the smoothed reach-end elevations from elevslope."""
    from dbfread import DBF
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("ComID")
        if comid is None:
            continue
        comid = int(comid)
        maxe, mine = elev.get(comid, (None, None))
        yield {
            "comid":       comid,
            "hydroseq":    int(rec["Hydroseq"]) if rec.get("Hydroseq") else None,
            "levelpathid": int(rec["LevelPathI"]) if rec.get("LevelPathI") else None,
            "streamlevel": int(rec["StreamLeve"]) if rec.get("StreamLeve") else None,
            "gnis_name":   gnis.get(comid),
            "lengthkm":    float(rec["LengthKM"]) if rec.get("LengthKM") else None,
            "maxelevsmo":  maxe,
            "minelevsmo":  mine,
        }


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--vpu", default=None,
                   help="comma-separated VPU ids to restrict to "
                        "(e.g. MA_02,MS_05); default = all CONUS")
    p.add_argument("--out", default=OUT_PATH, help=f"default: {OUT_PATH}")
    p.add_argument("--list", action="store_true",
                   help="discover + print the VPU archive set, then exit")
    args = p.parse_args()

    only = {v.strip() for v in args.vpu.split(",")} if args.vpu else None
    print("[discover] listing EPA NHDPlusV21 S3 bucket ...")
    regions = discover_regions(only)
    if not regions:
        print("ERROR: no VPU archives discovered (check egress / --vpu)",
              file=sys.stderr)
        return 1
    print(f"[discover] {len(regions)} VPU(s): "
          f"{', '.join(r['id'] for r in regions)}")
    if args.list:
        for r in regions:
            print(f"  {r['id']}: {r['vaa']}\n        {r['snap']}")
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    total = 0
    with tempfile.TemporaryDirectory() as tmp, \
         gzip.open(args.out, "wt", newline="", encoding="utf-8") as gz_f:
        writer = csv.DictWriter(gz_f, fieldnames=OUT_COLUMNS)
        writer.writeheader()

        for r in regions:
            print(f"[{r['id']}] {r['label']}")
            vaa_arch = os.path.join(tmp, f"{r['id']}_vaa.7z")
            snap_arch = os.path.join(tmp, f"{r['id']}_snap.7z")
            download(r["vaa"], vaa_arch)
            download(r["snap"], snap_arch)

            print("  extracting NHDFlowline.dbf for GNIS_Name ...")
            flow_dbf = extract_dbf(snap_arch, tmp, "NHDFlowline")
            gnis = gnis_names_from_flowline(flow_dbf)
            print(f"  loaded {len(gnis):,} named flowlines")

            print("  extracting elevslope.dbf for reach elevations ...")
            elev_dbf = extract_dbf(vaa_arch, tmp, "elevslope")
            elev = elev_from_elevslope(elev_dbf)
            print(f"  loaded {len(elev):,} reach elevations")

            print("  extracting PlusFlowlineVAA.dbf and joining ...")
            vaa_dbf = extract_dbf(vaa_arch, tmp, "PlusFlowlineVAA")
            count = 0
            for row in vaa_rows(vaa_dbf, gnis, elev):
                writer.writerow(row)
                count += 1
            print(f"  wrote {count:,} rows")
            total += count

            for f in (vaa_arch, snap_arch, vaa_dbf, flow_dbf, elev_dbf):
                try:
                    os.remove(f)
                except OSError:
                    pass

    size = os.path.getsize(args.out)
    print(f"\n[done] {total:,} rows -> {args.out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
