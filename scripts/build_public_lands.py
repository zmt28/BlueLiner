#!/usr/bin/env python3
"""
Build data/public_lands/public_lands.geojson.gz from the bundled
PAD-US 4.0 geodatabase.

PAD-US 4.0 ships as a single ~1.6 GB geodatabase ZIP on ScienceBase
(https://www.sciencebase.gov/catalog/item/652ef930d34edd15305a9b03).
ScienceBase routes files >1 GB through a captcha-gated, one-shot
download system, so a programmatic fetch from this script is not
possible -- the operator downloads the ZIP manually once and the
script reads from disk.

Workflow:
    1) Open the ScienceBase item URL above in a browser.
    2) Click `PADUS4_0Geodatabase.zip` in Attached Files. Solve the
       captcha; wait for the "Download File" button to activate.
    3) Save the file to data/public_lands/PADUS4_0Geodatabase.zip
       (or anywhere else and pass --gdb-zip <path>).
    4) Run this script. ~10-15 min wall clock; peak RSS ~8-12 GB
       during the pyogrio read (PAD-US geometries are complex --
       Alaska parks especially).

Each output feature carries the canonical six-field schema the
runtime expects:
    unit_name, manager_type, manager_name, designation,
    public_access, state_nm

Geometry is the (Multi)Polygon simplified at SIMPLIFY_TOL (the same
~33 m tolerance used by the clickable_streams builder).

Dev dependencies (NOT in runtime requirements.txt):
    pip install httpx pyogrio shapely

Run from repo root:
    python scripts/build_public_lands.py [--gdb-zip PATH] [--out PATH]

The output is hosted on Cloudflare R2 alongside the other bundled
data files -- see CONTRIBUTING.md "Refreshing PAD-US" for the upload
+ redeploy playbook.
"""

import argparse
import gzip
import json
import os
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "public_lands")
DEFAULT_OUT_PATH = os.path.join(OUT_DIR, "public_lands.geojson.gz")
DEFAULT_GDB_ZIP = os.path.join(OUT_DIR, "PADUS4_0Geodatabase.zip")
SCIENCEBASE_URL = (
    "https://www.sciencebase.gov/catalog/item/652ef930d34edd15305a9b03")

# Same simplification tolerance as build_clickable_streams.py -- enough
# to drop redundant vertices without altering which parcel a click
# falls in.
SIMPLIFY_TOL = 0.0003

# Layers inside the GDB we actually want. PAD-US's "Combined_Fee"
# is the bulk of fee-simple public ownership; "Combined_Easement" is
# the conservation-easement-on-private-land slice. Skipping:
#   - "Combined_Marine" (saltwater MPAs, irrelevant for stream fishing)
#   - "Combined_Designation" (overlays the same land as Fee with extra
#     wilderness/special-area designations; including it would
#     double-render the polygon)
#   - "Combined_Proclamation" (administrative boundaries, not parcels)
# We match by substring on the layer name so PAD-US 4.1's "Fee" /
# "Easement" rename (if it lands that way) doesn't require a code
# change here.
TARGET_LAYER_PATTERNS = ("Combined_Fee", "Combined_Easement")
SKIP_LAYER_PATTERNS = ("Marine",)

# PAD-US source field -> canonical client-facing field. Anything not
# listed here is dropped -- saves ~30-40% of the output payload.
FIELD_MAP = {
    "Unit_Nm":     "unit_name",
    "Mang_Type":   "manager_type",
    "Mang_Name":   "manager_name",
    "Des_Tp":      "designation",
    "Pub_Access":  "public_access",
    "State_Nm":    "state_nm",
}


def select_layers(gdb_path: str) -> list[str]:
    """Pick the GDB layers we want to ingest, matching by substring so
    minor PAD-US renames don't break the build."""
    import pyogrio
    raw = pyogrio.list_layers(gdb_path)
    # pyogrio.list_layers returns a 2D array of [layer_name, geometry_type]
    names = [str(row[0]) for row in raw]
    chosen: list[str] = []
    for name in names:
        if any(skip in name for skip in SKIP_LAYER_PATTERNS):
            continue
        if any(want in name for want in TARGET_LAYER_PATTERNS):
            chosen.append(name)
    if not chosen:
        raise SystemExit(
            f"No matching layers in {gdb_path}.\n"
            f"  Looked for any of {TARGET_LAYER_PATTERNS} (excluding "
            f"{SKIP_LAYER_PATTERNS}).\n"
            f"  Found layers: {names}\n"
            f"PAD-US may have renamed its layers; update "
            f"TARGET_LAYER_PATTERNS in this script.")
    return chosen


def emit_features(layer_name: str, gdb_path: str, out, written):
    """Stream features from one GDB layer through simplification +
    attribute pruning into the gzipped output. Mutates `written` (the
    leading-comma flag) and returns the running total."""
    from shapely.geometry import mapping, shape
    import pyogrio

    print(f"[{layer_name}] reading ...")
    gdf = pyogrio.read_dataframe(gdb_path, layer=layer_name)
    if gdf is None or gdf.empty:
        print(f"  empty layer, skipping")
        return written
    print(f"  {len(gdf):,} rows")

    n_layer = 0
    for raw in gdf.iterfeatures():
        props_in = raw.get("properties") or {}
        props_out = {client: props_in.get(src)
                     for src, client in FIELD_MAP.items()}
        if not props_out.get("manager_type"):
            continue        # no point rendering a parcel with no color tier
        geom_dict = raw.get("geometry")
        if not geom_dict:
            continue
        try:
            g = shape(geom_dict).buffer(0)         # fix self-intersections
            if g.is_empty:
                continue
            g = g.simplify(SIMPLIFY_TOL, preserve_topology=False)
            if g.is_empty:
                continue
        except Exception:
            continue
        feat = {
            "type": "Feature",
            "properties": props_out,
            "geometry": mapping(g),
        }
        if written["any"]:
            out.write(",")
        json.dump(feat, out, separators=(",", ":"))
        written["any"] = True
        written["total"] += 1
        n_layer += 1
    print(f"  +{n_layer:,} features written (running total "
          f"{written['total']:,})")
    return written


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--gdb-zip", default=DEFAULT_GDB_ZIP,
                   help=f"Path to PADUS4_0Geodatabase.zip "
                        f"(default: {DEFAULT_GDB_ZIP})")
    p.add_argument("--out", default=DEFAULT_OUT_PATH,
                   help=f"Output gzipped GeoJSON path "
                        f"(default: {DEFAULT_OUT_PATH})")
    args = p.parse_args()

    if not os.path.exists(args.gdb_zip):
        print(f"ERROR: PAD-US geodatabase not found at {args.gdb_zip}",
              file=sys.stderr)
        print(file=sys.stderr)
        print("Download it manually (~1.6 GB, one-time):", file=sys.stderr)
        print(f"  1. Open {SCIENCEBASE_URL}", file=sys.stderr)
        print("  2. Click 'PADUS4_0Geodatabase.zip' in Attached Files.",
              file=sys.stderr)
        print("  3. Solve the captcha + click 'Download File'.",
              file=sys.stderr)
        print(f"  4. Save the .zip to {args.gdb_zip}", file=sys.stderr)
        print("     (or pass --gdb-zip <other-path>)", file=sys.stderr)
        print("     macOS Archive Utility may have auto-extracted the .zip; "
              "that's fine -- point --gdb-zip at the extracted folder.",
              file=sys.stderr)
        print("Then re-run this script.", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        # Accept either a ZIP archive or an already-extracted directory
        # (macOS Archive Utility auto-extracts ZIPs by default, leaving
        # a folder instead of the original .zip). If the input is a
        # directory, skip the unzip step and search it for the .gdb.
        if os.path.isdir(args.gdb_zip):
            search_root = args.gdb_zip
            print(f"[input] {args.gdb_zip} (already extracted)")
        else:
            print(f"[unzip] {args.gdb_zip} -> {tmp}/")
            with zipfile.ZipFile(args.gdb_zip) as z:
                z.extractall(tmp)
            search_root = tmp
        # Find the .gdb directory in the extracted (or pre-extracted)
        # tree. ScienceBase nests it one level deep, e.g.
        # "PADUS4_0Geodatabase/PADUS4_0Geodatabase.gdb".
        gdb_paths = []
        for root, dirs, _ in os.walk(search_root):
            for d in dirs:
                if d.endswith(".gdb"):
                    gdb_paths.append(os.path.join(root, d))
        if not gdb_paths:
            print(f"ERROR: no .gdb directory found under {search_root}",
                  file=sys.stderr)
            return 1
        gdb_path = gdb_paths[0]
        print(f"[gdb] {gdb_path}")

        layers = select_layers(gdb_path)
        print(f"[layers] {layers}")

        written = {"any": False, "total": 0}
        with gzip.open(args.out, "wt", encoding="utf-8") as out:
            out.write('{"type":"FeatureCollection","features":[')
            for layer in layers:
                emit_features(layer, gdb_path, out, written)
            out.write("]}")

    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\n[done] {written['total']:,} parcels -> {args.out} "
          f"({size_mb:.1f} MB gz)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
