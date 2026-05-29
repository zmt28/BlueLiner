#!/usr/bin/env python3
"""MVT-vs-GeoJSON tile-size measurement harness (B-after-B2 MVT spike).

Compares, for representative tiles over a data-dense area, the wire size of
the CURRENT per-viewport GeoJSON payload vs an MVT-encoded tile carrying the
same features (with the same per-zoom stream-order filter the API uses). Not
production code — a reproducible measurement so the findings cite real numbers.

Usage:
    gunzip -kc data/nhdplus/clickable_streams.geojson.gz > /tmp/clk.geojson
    pip install mapbox-vector-tile mercantile shapely
    python3 spike/mvt/measure_tiles.py /tmp/clk.geojson

Deps are pure-Python (mapbox-vector-tile, mercantile) + shapely (already a
runtime dep). Nothing here is imported by the app.
"""
import json
import gzip
import sys
import time

import mercantile
import mapbox_vector_tile as mvt
from shapely.geometry import shape, box, mapping


def feature_bbox(coords, _gtype):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for x in c:
                walk(x)

    walk(coords)
    return min(xs), min(ys), max(xs), max(ys)


def min_order_for_zoom(z):
    # Mirrors main.py _min_order_for_zoom.
    return 1 if z >= 14 else 2 if z >= 12 else 3 if z >= 10 else 5 if z >= 8 else 6


def gz(b):
    return len(gzip.compress(b, 9))


def main(path):
    feats = json.load(open(path))["features"]
    items = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        items.append((feature_bbox(g["coordinates"], g["type"]), f))
    print(f"loaded {len(items)} features from {path}\n")

    # Representative tiles over a dense MD area (Gunpowder / Baltimore).
    samples = [(-76.6, 39.3, 8), (-76.6, 39.3, 10), (-76.6, 39.3, 12), (-76.6, 39.3, 13)]
    hdr = f"{'tile':<16}{'feats':>7}{'GeoJSON KB':>12}{'gz':>8}{'MVT KB':>9}{'MVT gz':>8}{'enc ms':>8}"
    print(hdr)
    print("-" * len(hdr))
    for lng, lat, z in samples:
        t = mercantile.tile(lng, lat, z)
        b = mercantile.bounds(t)
        tb = box(b.west, b.south, b.east, b.north)
        mo = min_order_for_zoom(z)
        sel = [
            f
            for (bb, f) in items
            if (f["properties"].get("streamorder") or 0) >= mo
            and not (bb[2] < b.west or bb[0] > b.east or bb[3] < b.south or bb[1] > b.north)
        ]
        keep = ("comid", "levelpathid", "gnis_name", "streamorder", "trout_class")
        fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": f["geometry"],
                    "properties": {k: f["properties"].get(k) for k in keep},
                }
                for f in sel
            ],
        }
        gj = json.dumps(fc, separators=(",", ":")).encode()

        mvt_feats = []
        for f in sel:
            try:
                clipped = shape(f["geometry"]).intersection(tb)
            except Exception:
                continue
            if clipped.is_empty:
                continue
            p = f["properties"]
            mvt_feats.append(
                {
                    "geometry": mapping(clipped),
                    "properties": {
                        k: p.get(k)
                        for k in ("levelpathid", "gnis_name", "streamorder", "trout_class")
                        if p.get(k) is not None
                    },
                }
            )
        t0 = time.time()
        pbf = mvt.encode(
            [{"name": "streams", "features": mvt_feats}],
            quantize_bounds=(b.west, b.south, b.east, b.north),
            extents=4096,
        )
        ms = (time.time() - t0) * 1000
        print(
            f"z{z} {t.x}/{t.y}".ljust(16)
            + f"{len(sel):>7}{len(gj) / 1024:>12.1f}{gz(gj) / 1024:>8.1f}"
            + f"{len(pbf) / 1024:>9.1f}{gz(pbf) / 1024:>8.1f}{ms:>8.0f}"
        )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/clk.geojson")
