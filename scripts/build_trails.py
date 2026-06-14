#!/usr/bin/env python3
"""
Build data/trails/trails.geojson.gz from the USGS National Map National
Digital Trails layer, filtered to segments running ALONGSIDE the BlueLiner
stream network.

Source (public domain, confirmed live via gis-endpoint-verify 2026-06-14):
    https://carto.nationalmap.gov/arcgis/rest/services/transportation/MapServer/37
    layer 37 'Trails' -- esriGeometryPolyline, ~548k features. Fields:
    name / maplabel, trailtype, trailsurface, lengthmiles, hikerpedestrian, ...

The app only wants trails an angler can walk to fish a river, so a trail
segment is kept only when it runs within --buffer-m of a reach in the stream
network (data/nhdplus/clickable_streams.geojson.gz -- the same network the map
renders). That cuts the national ~548k down to the riverside subset and keeps
the tileset small. Distance is measured in EPSG:5070 (CONUS Albers, meters).

Runs in the DATA-BUILD environment (CI / a dev box with geopandas + open
egress) -- NOT the runtime image, and NOT the Claude Code sandbox (its egress
allowlist blocks carto.nationalmap.gov). Memory stays bounded: the stream
network is loaded once (with a spatial index) and trails are streamed in
pages, each page joined and written before the next is fetched.

Output: data/trails/trails.geojson.gz, then PMTiles via
scripts/build_trail_tiles.sh, both published to R2. The client flips the
layer on with VITE_TRAILS_TILES_URL.

Dev dependencies (NOT in runtime requirements.txt):
    pip install httpx geopandas shapely

Run from repo root:
    python scripts/build_trails.py [--streams PATH] [--out PATH]
                                   [--buffer-m 150] [--foot-only]
"""

import argparse
import gzip
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "trails")
DEFAULT_OUT = os.path.join(OUT_DIR, "trails.geojson.gz")
DEFAULT_STREAMS = os.path.join(
    ROOT, "data", "nhdplus", "clickable_streams.geojson.gz")

TRAILS_LAYER = ("https://carto.nationalmap.gov/arcgis/rest/services/"
                "transportation/MapServer/37")
QUERY_URL = f"{TRAILS_LAYER}/query"

# Only the columns we surface (saves payload + fetch time). objectid is the
# keyset paging cursor; the others normalize to the client schema below.
OUT_FIELDS = "objectid,name,maplabel,trailtype,trailsurface,lengthmiles"
OID_FIELD = "objectid"
PAGE_SIZE = 2000

# Distance + geometry knobs. 150 m catches a trail that parallels a bank
# (towpaths, riverside rail-trails) without pulling in every ridge trail a
# few hundred meters upslope. Simplify/round match the streams builder.
DEFAULT_BUFFER_M = 150.0
SIMPLIFY_TOL_M = 10.0     # in EPSG:5070 meters
COORD_PRECISION = 5       # ~1.1 m

# trailtype is coded "Terra Trail" / "Water Trail" / "Snow Trail"; relabel
# Terra (the overwhelming majority) to a plain "Trail" for the popup.
TRAILTYPE_LABEL = {
    "Terra Trail": "Trail",
    "Water Trail": "Water trail",
    "Snow Trail": "Snow trail",
}

UA = {"User-Agent": "Blueliner-databuild/1.0 (+https://blueliner.app)"}


def _clean(v) -> str | None:
    if v in (None, "", "None"):
        return None
    s = str(v).strip()
    return s or None


def fetch_trail_pages(where: str):
    """Yield lists of ArcGIS GeoJSON features, paged by objectid keyset
    (resultOffset paging is unreliable on TNM MapServer layers). Bounded
    retries per page; raises if a page keeps failing so a partial build
    never silently publishes."""
    import httpx
    last = -1
    with httpx.Client(timeout=60.0, headers=UA, follow_redirects=True) as c:
        while True:
            params = {
                "where": f"({where}) AND {OID_FIELD} > {last}",
                "orderByFields": f"{OID_FIELD} ASC",
                "outFields": OUT_FIELDS,
                "resultRecordCount": str(PAGE_SIZE),
                "f": "geojson",
                "outSR": "4326",
                "returnGeometry": "true",
            }
            data = None
            for attempt in range(4):
                try:
                    r = c.get(QUERY_URL, params=params)
                    if r.status_code == 200:
                        data = r.json()
                        break
                except httpx.TransportError:
                    pass
                time.sleep(2 ** attempt)
            if data is None:
                raise SystemExit(f"trails fetch failed at objectid > {last}")
            feats = data.get("features") or []
            if not feats:
                return
            ids = [f.get("properties", {}).get(OID_FIELD) for f in feats]
            ids = [i for i in ids if isinstance(i, int)]
            if not ids:
                return
            yield feats
            mx = max(ids)
            if mx <= last:        # server ignored the keyset -> stop
                return
            last = mx
            # NB: do NOT stop on len(feats) < PAGE_SIZE -- TNM caps a page at
            # its own maxRecordCount (often 1000), which can be below
            # PAGE_SIZE; that would truncate the fetch. The keyset advances
            # until a request past the last id returns an empty page.


def load_stream_index(streams_path: str):
    """Load the clickable-stream network into an EPSG:5070 GeoDataFrame and
    build its spatial index (used as the join target)."""
    import geopandas as gpd
    with gzip.open(streams_path, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    gdf = gpd.GeoDataFrame.from_features(fc.get("features", []), crs="EPSG:4326")
    if gdf.empty:
        raise SystemExit(f"no stream features in {streams_path}")
    gdf = gdf.to_crs(epsg=5070)[["geometry"]]
    gdf.sindex  # materialize the index
    print(f"[streams] {len(gdf):,} reaches loaded (EPSG:5070)")
    return gdf


def _round_coords(geom: dict, precision: int = COORD_PRECISION) -> dict:
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if coords is None:
        return geom

    def rpt(p):
        return [round(p[0], precision), round(p[1], precision)]

    if gtype == "LineString":
        new = [rpt(p) for p in coords]
    elif gtype == "MultiLineString":
        new = [[rpt(p) for p in line] for line in coords]
    else:
        return geom
    return {"type": gtype, "coordinates": new}


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--streams", default=DEFAULT_STREAMS,
                   help=f"clickable-streams geojson.gz (default: {DEFAULT_STREAMS})")
    p.add_argument("--out", default=DEFAULT_OUT,
                   help=f"output gzipped GeoJSON (default: {DEFAULT_OUT})")
    p.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M,
                   help="keep trails within this many metres of a reach")
    p.add_argument("--foot-only", action="store_true",
                   help="restrict the fetch to hiker/pedestrian trails")
    args = p.parse_args()

    if not os.path.exists(args.streams):
        print(f"ERROR: stream network not found at {args.streams}\n"
              f"  run scripts/build_clickable_streams.py first (or pass "
              f"--streams).", file=sys.stderr)
        return 1

    import geopandas as gpd
    from shapely.geometry import mapping

    streams = load_stream_index(args.streams)
    where = "hikerpedestrian='Y'" if args.foot_only else "1=1"

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    kept = scanned = 0
    written = {"any": False}
    with gzip.open(args.out, "wt", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[')
        for page in fetch_trail_pages(where):
            scanned += len(page)
            tg = gpd.GeoDataFrame.from_features(page, crs="EPSG:4326")
            if tg.empty:
                continue
            tg = tg.to_crs(epsg=5070)
            # Keep a trail only if a reach lies within buffer_m of it.
            hit = gpd.sjoin_nearest(
                tg, streams, max_distance=args.buffer_m, how="inner")
            hit = hit[~hit.index.duplicated(keep="first")]
            if hit.empty:
                continue
            riverside = tg.loc[hit.index]
            riverside["geometry"] = riverside.geometry.simplify(
                SIMPLIFY_TOL_M, preserve_topology=False)
            riverside = riverside.to_crs(epsg=4326)
            for _, row in riverside.iterrows():
                g = row.geometry
                if g is None or g.is_empty:
                    continue
                props = {
                    "name": _clean(row.get("maplabel")) or _clean(row.get("name")),
                    "trail_type": TRAILTYPE_LABEL.get(
                        _clean(row.get("trailtype")), _clean(row.get("trailtype"))),
                    "surface": _clean(row.get("trailsurface")),
                }
                lm = row.get("lengthmiles")
                if isinstance(lm, (int, float)) and lm > 0:
                    props["length_mi"] = round(float(lm), 2)
                props = {k: v for k, v in props.items() if v is not None}
                feat = {"type": "Feature", "properties": props,
                        "geometry": _round_coords(mapping(g))}
                if written["any"]:
                    out.write(",")
                json.dump(feat, out, separators=(",", ":"))
                written["any"] = True
                kept += 1
            print(f"  scanned {scanned:,} | kept {kept:,}", flush=True)
        out.write("]}")

    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\n[done] {kept:,} riverside trail segments (of {scanned:,} "
          f"scanned) -> {args.out} ({size_mb:.1f} MB gz)")
    if kept == 0:
        print("ERROR: zero trails kept -- check the buffer / stream network",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
