#!/usr/bin/env python3
"""
One-time prep: build data/nhdplus/vaa.csv.gz from NHDPlusV2 archives.

Per region, downloads two 7z components, extracts only the DBFs we
need, joins them on ComID, and appends to a single gzipped CSV:

  - NHDPlusAttributes / PlusFlowlineVAA.dbf
      -> Hydroseq, LevelPathID, StreamLevel, LengthKM (the routing
         attributes that drive same-river filtering)
  - NHDSnapshot / NHDFlowline.dbf
      -> GNIS_Name (NHD's authoritative name for the reach; lives in
         the geometry-table component, not VAA)

Output is committed to the repo. Runtime loads it once into a
Postgres table on first boot and never touches EPA. Re-run only when
expanding regions.

Dev dependencies (NOT in runtime requirements.txt):
    pip install dbfread py7zr httpx

Run from repo root:
    python scripts/build_nhdplus_vaa.py
"""

import csv
import gzip
import os
import sys
import tempfile

import httpx
import py7zr
from dbfread import DBF

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "data", "nhdplus", "vaa.csv.gz")
S3_BASE = "https://dmap-data-commons-ow.s3.amazonaws.com/NHDPlusV21/Data"

# Per region: VAA archive + NHDSnapshot archive (NHDFlowline.dbf for
# GNIS_Name). Add entries here when expanding coverage.
REGIONS = [
    {"id": "MA_02", "label": "Mid-Atlantic (HUC-02)",
     "vaa":  f"{S3_BASE}/NHDPlusMA/NHDPlusV21_MA_02_NHDPlusAttributes_09.7z",
     "snap": f"{S3_BASE}/NHDPlusMA/NHDPlusV21_MA_02_NHDSnapshot_04.7z"},
    {"id": "MS_05", "label": "Ohio (HUC-05)",
     "vaa":  f"{S3_BASE}/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDPlusAttributes_09.7z",
     "snap": f"{S3_BASE}/NHDPlusMS/NHDPlus05/NHDPlusV21_MS_05_NHDSnapshot_06.7z"},
]

OUT_COLUMNS = ["comid", "hydroseq", "levelpathid", "streamlevel",
               "gnis_name", "lengthkm"]


def download(url: str, dest: str) -> None:
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
    """Extract only `<name>.dbf` (not the heavy SHP) from a 7z archive."""
    with py7zr.SevenZipFile(archive, mode="r") as z:
        targets = [n for n in z.getnames() if n.endswith(f"{name}.dbf")]
        if not targets:
            raise RuntimeError(
                f"{name}.dbf not in {os.path.basename(archive)}; "
                f"first entries: {z.getnames()[:5]}")
        z.extract(path=workdir, targets=targets)
        return os.path.join(workdir, targets[0])


def gnis_names_from_flowline(dbf_path: str) -> dict[int, str]:
    """COMID -> GNIS_Name (empties dropped)."""
    out: dict[int, str] = {}
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("COMID")
        if comid is None:
            continue
        name = rec.get("GNIS_NAME")
        if name:
            out[int(comid)] = str(name).strip()
    return out


def vaa_rows(dbf_path: str, gnis: dict[int, str]):
    """Yield projected VAA dicts joined with GNIS_Name from the snapshot."""
    for rec in DBF(dbf_path, ignore_missing_memofile=True):
        comid = rec.get("ComID")
        if comid is None:
            continue
        comid = int(comid)
        yield {
            "comid":       comid,
            "hydroseq":    int(rec["Hydroseq"]) if rec.get("Hydroseq") else None,
            "levelpathid": int(rec["LevelPathI"]) if rec.get("LevelPathI") else None,
            "streamlevel": int(rec["StreamLeve"]) if rec.get("StreamLeve") else None,
            "gnis_name":   gnis.get(comid),
            "lengthkm":    float(rec["LengthKM"]) if rec.get("LengthKM") else None,
        }


def main() -> int:
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    total = 0

    with tempfile.TemporaryDirectory() as tmp, \
         gzip.open(OUT_PATH, "wt", newline="", encoding="utf-8") as gz_f:
        writer = csv.DictWriter(gz_f, fieldnames=OUT_COLUMNS)
        writer.writeheader()

        for r in REGIONS:
            print(f"[{r['id']}] {r['label']}")
            vaa_arch = os.path.join(tmp, f"{r['id']}_vaa.7z")
            snap_arch = os.path.join(tmp, f"{r['id']}_snap.7z")
            download(r["vaa"], vaa_arch)
            download(r["snap"], snap_arch)

            print("  extracting NHDFlowline.dbf for GNIS_Name ...")
            flow_dbf = extract_dbf(snap_arch, tmp, "NHDFlowline")
            gnis = gnis_names_from_flowline(flow_dbf)
            print(f"  loaded {len(gnis):,} named flowlines")

            print("  extracting PlusFlowlineVAA.dbf and joining ...")
            vaa_dbf = extract_dbf(vaa_arch, tmp, "PlusFlowlineVAA")
            count = 0
            for row in vaa_rows(vaa_dbf, gnis):
                writer.writerow(row)
                count += 1
            print(f"  wrote {count:,} rows")
            total += count

            for f in (vaa_arch, snap_arch, vaa_dbf, flow_dbf):
                try:
                    os.remove(f)
                except OSError:
                    pass

    size = os.path.getsize(OUT_PATH)
    print(f"\n[done] {total:,} rows -> {OUT_PATH} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
