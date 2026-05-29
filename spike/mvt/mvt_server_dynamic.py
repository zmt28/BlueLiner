"""Path B reference — a no-PostGIS dynamic MVT endpoint (MVT spike).

Reference only; NOT wired into main.py. Shows how a `/tiles/streams/{z}/{x}/{y}.pbf`
route can reuse Blueliner's EXISTING storage (GeoJSON text + the GiST `box`
index) to serve vector tiles, encoding in Python with `mapbox-vector-tile`.
No PostGIS, no schema change.

Add to requirements.txt for this path:
    mapbox-vector-tile
    mercantile

The win over the current `/api/clickable_streams?bbox=` response is that tiles
are addressable + URL-cacheable (Cloudflare edge via s-maxage, plus a
service-worker `/tiles/*` strategy), work at every zoom, and decode off the
main thread in MapLibre. The cost is the per-tile encode on a cache miss
(measured 1-43 ms; see measure_tiles.py) on the single free web worker — fine
behind edge caching for static-ish data.
"""
from __future__ import annotations

import asyncio

import mercantile
import mapbox_vector_tile as mvt
from fastapi import Response
from shapely.geometry import shape, box, mapping

# In the real app these come from the existing modules:
#   import db
#   from main import app, _min_order_for_zoom, _cached_response


def _min_order_for_zoom(z: int) -> int:
    return 1 if z >= 14 else 2 if z >= 12 else 3 if z >= 10 else 5 if z >= 8 else 6


# @app.get("/tiles/streams/{z}/{x}/{y}.pbf")
async def stream_tile(z: int, x: int, y: int) -> Response:
    """One vector tile of the clickable-stream network.

    Reuses db.query_clickable_streams (the GiST bbox query) for the tile's
    bbox, clips each reach to the tile, and MVT-encodes. Mirrors the
    per-zoom stream-order filter the GeoJSON endpoint already applies.
    """
    if not (0 <= z <= 22):
        return Response(status_code=400)
    b = mercantile.bounds(mercantile.Tile(x, y, z))
    tile_box = box(b.west, b.south, b.east, b.north)
    min_order = _min_order_for_zoom(z)

    # rows: list of dicts with "geometry" (parsed) + props, exactly what
    # db.query_clickable_streams already returns today.
    rows = await asyncio.to_thread(
        _query_clickable_streams_placeholder, b.west, b.south, b.east, b.north, min_order
    )

    feats = []
    for r in rows:
        try:
            clipped = shape(r["geometry"]).intersection(tile_box)
        except Exception:
            continue
        if clipped.is_empty:
            continue
        feats.append(
            {
                "geometry": mapping(clipped),
                "properties": {
                    # levelpathid drives promoteId/feature-state highlight on
                    # the client; keep the styling/identity props only.
                    k: r[k]
                    for k in ("levelpathid", "gnis_name", "streamorder", "trout_class")
                    if r.get(k) is not None
                },
            }
        )

    pbf = mvt.encode(
        [{"name": "streams", "features": feats}],
        quantize_bounds=(b.west, b.south, b.east, b.north),
        extents=4096,
    )
    # Edge + browser caching: static-ish data, so a long s-maxage gives a high
    # CDN hit rate and keeps the encode off the hot path. In the real app,
    # route this through _cached_response() for ETag + stale-while-revalidate.
    return Response(
        content=pbf,
        media_type="application/vnd.mapbox-vector-tile",
        headers={
            "Cache-Control": "public, max-age=600, s-maxage=86400, "
            "stale-while-revalidate=86400"
        },
    )


def _query_clickable_streams_placeholder(w, s, e, n, min_order):  # pragma: no cover
    """Stand-in for db.query_clickable_streams(w, s, e, n, min_order).

    The real function runs:
        SELECT comid, levelpathid, gnis_name, streamorder, trout_class, geom
        FROM clickable_streams
        WHERE streamorder >= %s
          AND bbox && box(point(%s,%s), point(%s,%s))
        ORDER BY streamorder DESC LIMIT 4000
    and json.loads() + trims each row's `geom`. No change needed for tiles.
    """
    raise NotImplementedError("reference only — call db.query_clickable_streams")
