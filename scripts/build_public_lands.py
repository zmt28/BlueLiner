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

# Geometry simplification + precision knobs. PAD-US polygons have far
# higher per-feature vertex counts than NHDPlus lines -- some
# MultiPolygons have 100+ rings (National Forest sub-boundaries,
# scattered BLM allotments). Iterating on a national build: streams'
# 0.0003 produced 1+ GB; 0.003 still produced 803 MB; 0.008 plus
# Easement-layer drop got to 559 MB. The remaining bulk is "Swiss
# cheese" private inholdings inside National Forests -- hundreds of
# tiny interior rings per feature, each at 3-10 vertices that
# simplify can't reduce further (DP won't go below 3 verts/ring).
# At 0.015 (~1.7 km at mid-latitudes) the exterior boundary plus the
# small-hole filter below compound to ~5-10x further reduction.
# Polygon shapes remain clearly legible at zoom 8-10 where the layer
# answers "which national forest is this"; at zoom 13+ you'll see
# light edge-blockiness, which is the right trade since at that zoom
# the angler is reading the stream geometry, not the forest boundary.
SIMPLIFY_TOL = 0.015
COORD_PRECISION = 4         # ~11 m -- below SIMPLIFY_TOL so lossless wrt it
# Drop parcels (and interior holes) smaller than this many sq-degrees.
# 1e-5 ≈ 120,000 m² ≈ ~30 acres at mid-latitudes. PAD-US units
# themselves are almost never sub-3-acre, but every National Forest
# has hundreds of small private-inholding interior rings that
# dominate vertex count without being visually meaningful at app
# zoom. Applied symmetrically to exterior parcels and interior holes.
MIN_AREA_DEG2 = 1e-5

# Layers inside the GDB we actually want. PAD-US 4.0's "PADUS4_0Fee"
# is the bulk of fee-simple public ownership; "PADUS4_0Easement" is
# the conservation-easement-on-private-land slice. Skipping:
#   - "PADUS4_0Marine" (saltwater MPAs, irrelevant for stream fishing)
#   - "PADUS4_0Designation" (overlays the same land as Fee with extra
#     wilderness/special-area designations; including it would
#     double-render the polygon)
#   - "PADUS4_0Proclamation" (administrative boundaries, not parcels)
#   - "PADUS4_0Combined_..." (the union-of-everything mega-layer --
#     would double-render with Fee + Easement)
# Match by substring so a PAD-US 4.1 rename like "PADUSFee" still
# works without a code change.
TARGET_LAYER_PATTERNS = ("PADUS4_0Fee", "PADUS4_0Easement")
SKIP_LAYER_PATTERNS = ("Marine", "Combined", "Designation", "Proclamation")

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

# PAD-US Pub_Access codes we keep. OA = Open Access (walk in, no
# permission needed); RA = Restricted Access (permit / seasonal /
# walk-in only). UK = Unknown is dropped because rendering
# guess-it's-public over private ranches sends anglers to locked
# gates; XA = Closed is dropped because there's nothing actionable on
# a fishing map about "definitely closed" parcels (the user assumes
# private/closed for anything uncolored anyway). The frontend keys
# its style off this field -- green for OA, dashed yellow for RA.
KEEP_PUB_ACCESS = {"OA", "RA"}


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


def _drop_small_holes(g, min_area: float = MIN_AREA_DEG2):
    """Strip interior rings (holes) smaller than `min_area` sq-degrees
    from each Polygon part. National Forests carry hundreds of
    tiny private-inholding holes that dominate vertex count without
    being visible at app zoom levels -- a 30-acre hole is less than
    one pixel at zoom 8, ~10 pixels at zoom 12, and the angler isn't
    making a fishing decision based on those specks anyway."""
    from shapely.geometry import Polygon, MultiPolygon
    def clean_poly(p):
        kept = [r for r in p.interiors
                if Polygon(r).area >= min_area]
        if len(kept) == len(p.interiors):
            return p
        return Polygon(p.exterior, kept)
    if g.geom_type == "Polygon":
        return clean_poly(g)
    if g.geom_type == "MultiPolygon":
        return MultiPolygon([clean_poly(p) for p in g.geoms])
    return g


def _round_coords(geom: dict, precision: int = COORD_PRECISION) -> dict:
    """Round every coordinate in a GeoJSON geometry to `precision`
    decimal places. shapely's `mapping()` emits full-precision floats
    (15+ digits) which json.dump then writes verbatim -- a 30-40%
    serialized-size win for free at the precision anglers care about."""
    coords = geom.get("coordinates")
    if coords is None:
        return geom
    gtype = geom.get("type")

    def rpt(pt):
        return [round(pt[0], precision), round(pt[1], precision)]

    def rring(ring):
        return [rpt(p) for p in ring]

    if gtype == "Polygon":
        new = [rring(r) for r in coords]
    elif gtype == "MultiPolygon":
        new = [[rring(r) for r in poly] for poly in coords]
    else:
        return geom        # not used for public_lands but harmless fall-through
    return {"type": gtype, "coordinates": new}


def emit_features(layer_name: str, gdb_path: str, out, written, stats):
    """Stream features from one GDB layer through simplification +
    attribute pruning + coordinate rounding into the gzipped output.
    Mutates `written` (the leading-comma flag) and `stats` (drop
    counters for end-of-run reporting)."""
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
        # Cheap filter first: Pub_Access decides whether a parcel even
        # belongs in the angler-facing layer. Filtering before the
        # shapely work skips ~30-40% of features on the expensive path.
        if props_in.get("Pub_Access") not in KEEP_PUB_ACCESS:
            stats["dropped_access"] += 1
            continue
        props_out = {client: props_in.get(src)
                     for src, client in FIELD_MAP.items()}
        if not props_out.get("manager_type"):
            stats["dropped_no_mgr"] += 1
            continue
        geom_dict = raw.get("geometry")
        if not geom_dict:
            stats["dropped_no_geom"] += 1
            continue
        try:
            g = shape(geom_dict).buffer(0)         # fix self-intersections
            if g.is_empty:
                stats["dropped_empty"] += 1
                continue
            g = g.simplify(SIMPLIFY_TOL, preserve_topology=False)
            if g.is_empty:
                stats["dropped_empty"] += 1
                continue
            g = _drop_small_holes(g)
            if g.area < MIN_AREA_DEG2:
                stats["dropped_tiny"] += 1
                continue
        except Exception:
            stats["dropped_error"] += 1
            continue
        feat = {
            "type": "Feature",
            "properties": props_out,
            "geometry": _round_coords(mapping(g)),
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

    # macOS's Archive Utility auto-extracts ZIPs by default, replacing
    # the .zip with a same-named directory. If the .zip default path
    # doesn't exist but the suffix-less folder does, transparently use
    # that instead so the operator doesn't have to remember the flag.
    if not os.path.exists(args.gdb_zip) and args.gdb_zip.endswith(".zip"):
        alt = args.gdb_zip[:-4]
        if os.path.exists(alt):
            print(f"[input] using auto-extracted directory: {alt}")
            args.gdb_zip = alt

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
        stats = {"dropped_access": 0, "dropped_no_mgr": 0,
                 "dropped_no_geom": 0, "dropped_empty": 0,
                 "dropped_tiny": 0, "dropped_error": 0}
        with gzip.open(args.out, "wt", encoding="utf-8") as out:
            out.write('{"type":"FeatureCollection","features":[')
            for layer in layers:
                emit_features(layer, gdb_path, out, written, stats)
            out.write("]}")

    size_mb = os.path.getsize(args.out) / 1e6
    dropped_total = sum(stats.values())
    print(f"\n[done] {written['total']:,} parcels -> {args.out} "
          f"({size_mb:.1f} MB gz)")
    if dropped_total:
        print(f"       dropped {dropped_total:,} "
              f"(access UK/XA: {stats['dropped_access']:,}, "
              f"tiny: {stats['dropped_tiny']:,}, "
              f"no manager: {stats['dropped_no_mgr']:,}, "
              f"empty/error: {stats['dropped_empty'] + stats['dropped_error']:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
