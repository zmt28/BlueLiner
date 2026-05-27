#!/usr/bin/env python3
"""
One-shot: reproject an existing public_lands.geojson.gz from
EPSG:5070 (NAD83 / CONUS Albers, the CRS PAD-US 4.0 ships in) to
EPSG:4326 (WGS84 lon/lat, what Leaflet + our Postgres queries
expect).

Why this exists: the original build_public_lands.py forgot to call
to_crs(4326) after pyogrio.read_dataframe, so the geojson we
uploaded to R2 has every coordinate in projected meters from a
continental origin (e.g. -80888.625, 1033148.94) instead of
decimal degrees (e.g. -77.0892, 39.6361). The build script now
includes the reprojection step, but rebuilding from the 1.6 GB GDB
is 15-20 min; this script reprojects the existing 489 MB geojson
in ~3-5 min by streaming features through pyproj.

Usage:
    python scripts/reproject_public_lands_geojson.py
    # default: reads data/public_lands/public_lands.geojson.gz,
    # writes data/public_lands/public_lands_4326.geojson.gz

    # explicit paths:
    python scripts/reproject_public_lands_geojson.py \\
        --input  /path/to/public_lands.geojson.gz \\
        --output /path/to/public_lands_4326.geojson.gz
"""

import argparse
import gzip
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_IN = os.path.join(
    ROOT, "data", "public_lands", "public_lands.geojson.gz")
DEFAULT_OUT = os.path.join(
    ROOT, "data", "public_lands", "public_lands_4326.geojson.gz")

# PAD-US 4.0 native CRS. If a future PAD-US vintage ships in a
# different CRS, override via --source-crs.
DEFAULT_SOURCE_CRS = "EPSG:5070"
TARGET_CRS = "EPSG:4326"
COORD_PRECISION = 4         # matches build_public_lands.py default


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--input", default=DEFAULT_IN,
                   help=f"Source gzipped geojson (default: {DEFAULT_IN})")
    p.add_argument("--output", default=DEFAULT_OUT,
                   help=f"Output gzipped geojson (default: {DEFAULT_OUT})")
    p.add_argument("--source-crs", default=DEFAULT_SOURCE_CRS,
                   help=f"Source CRS (default: {DEFAULT_SOURCE_CRS})")
    args = p.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 1

    import ijson
    from pyproj import Transformer

    # always_xy=True forces lon-then-lat output regardless of the
    # axis order the CRS authority defines. EPSG:4326 is lat,lon by
    # the standard but GeoJSON expects lon,lat; always_xy keeps us
    # consistent with the rest of the pipeline.
    transformer = Transformer.from_crs(
        args.source_crs, TARGET_CRS, always_xy=True)

    def reproject_ring(ring):
        # Batch-transform: pyproj is ~50x faster transforming an
        # array of points than calling .transform() per-point.
        xs = [pt[0] for pt in ring]
        ys = [pt[1] for pt in ring]
        nx, ny = transformer.transform(xs, ys)
        return [[round(x, COORD_PRECISION), round(y, COORD_PRECISION)]
                for x, y in zip(nx, ny)]

    def reproject_geom(geom):
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if not coords:
            return geom
        if gtype == "Polygon":
            return {"type": "Polygon",
                    "coordinates": [reproject_ring(r) for r in coords]}
        if gtype == "MultiPolygon":
            return {"type": "MultiPolygon",
                    "coordinates": [[reproject_ring(r) for r in poly]
                                    for poly in coords]}
        return geom

    in_size_mb = os.path.getsize(args.input) / 1e6
    print(f"[reproject] {args.input} ({in_size_mb:.1f} MB)")
    print(f"[reproject] {args.source_crs} -> {TARGET_CRS}")
    print(f"[reproject] writing -> {args.output}")

    count = 0
    started = time.time()
    last_report = started

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with gzip.open(args.input, "rb") as fin, \
         gzip.open(args.output, "wt", encoding="utf-8") as fout:
        fout.write('{"type":"FeatureCollection","features":[')
        first = True
        for feat in ijson.items(fin, "features.item"):
            geom = feat.get("geometry") or {}
            new_feat = {
                "type": "Feature",
                "properties": feat.get("properties", {}) or {},
                "geometry": reproject_geom(geom),
            }
            if not first:
                fout.write(",")
            json.dump(new_feat, fout, separators=(",", ":"), default=float)
            first = False
            count += 1
            now = time.time()
            if now - last_report >= 5:
                rate = count / max(now - started, 0.001)
                print(f"  {count:>7,} features  "
                      f"({rate:.0f}/s, {(now - started)/60:.1f} min)")
                last_report = now
        fout.write("]}")

    out_size_mb = os.path.getsize(args.output) / 1e6
    elapsed = time.time() - started
    print(f"\n[done] reprojected {count:,} features in {elapsed/60:.1f} min")
    print(f"[done] output: {args.output} ({out_size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
