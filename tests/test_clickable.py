"""Phase B backend: clickable-stream network load + viewport/tier query
+ endpoint. Offline -- a tiny synthetic GeoJSON fixture, no network."""

import asyncio
import gzip
import json
from types import SimpleNamespace

import db
import main


def _fixture(tmp_path):
    """Three streams with known bboxes/orders/classes."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {
            "comid": 1, "levelpathid": 100, "gnis_name": "Big River",
            "streamorder": 6, "trout_class": None},
         "geometry": {"type": "LineString",
                      "coordinates": [[-77.8, 40.8], [-77.6, 40.9]]}},
        {"type": "Feature", "properties": {
            "comid": 2, "levelpathid": 200, "gnis_name": "Penns Creek",
            "streamorder": 4, "trout_class": "class_a"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-77.5, 40.7], [-77.4, 40.75]]}},
        {"type": "Feature", "properties": {
            "comid": 3, "levelpathid": 300, "gnis_name": "Tiny Headwater",
            "streamorder": 1, "trout_class": "wild_reproduction"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-77.45, 40.72], [-77.44, 40.73]]}},
    ]}
    path = tmp_path / "clk.geojson.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(fc, f)
    return str(path)


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "clk.db"))
    db.init_db()


def test_bulk_load_idempotent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    path = _fixture(tmp_path)
    assert db.bulk_load_clickable_streams(path) == 3
    assert db.bulk_load_clickable_streams(path) == 0      # already loaded
    assert db.clickable_loaded() is True


def test_viewport_and_tier_filtering(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    db.bulk_load_clickable_streams(_fixture(tmp_path))
    box = (-78.0, 40.6, -77.3, 41.0)                      # covers all three
    assert len(db.query_clickable_streams(*box, min_order=1)) == 3
    assert len(db.query_clickable_streams(*box, min_order=4)) == 2  # drops order-1
    assert len(db.query_clickable_streams(*box, min_order=6)) == 1  # Big River only
    # viewport that excludes Big River's bbox
    east_box = (-77.55, 40.65, -77.3, 41.0)
    names = {r["gnis_name"] for r in db.query_clickable_streams(*east_box, min_order=1)}
    assert "Big River" not in names and "Penns Creek" in names
    # geometry round-trips
    r = db.query_clickable_streams(*box, min_order=6)[0]
    assert r["geometry"]["type"] == "LineString"
    assert r["trout_class"] is None and r["streamorder"] == 6


def test_min_order_for_zoom_tiers():
    f = main._min_order_for_zoom
    assert f(7) == 6 and f(8) == 5 and f(9) == 4
    assert f(10) == 3 and f(12) == 2 and f(13) == 1
    assert f(16) == 1                                    # clamps


def test_endpoint_returns_filtered_featurecollection(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    db.bulk_load_clickable_streams(_fixture(tmp_path))
    req = SimpleNamespace(headers={})
    # zoom 13 -> min_order 1 -> all three in a wide bbox
    resp = asyncio.run(main.api_clickable_streams(
        req, bbox="-78.0,40.6,-77.3,41.0", zoom=13))
    fc = json.loads(resp.body)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 3
    assert {"comid", "levelpathid", "gnis_name", "streamorder",
            "trout_class"} <= set(fc["features"][0]["properties"])
    # zoom 9 -> min_order 4 -> Big River + Penns Creek only
    resp2 = asyncio.run(main.api_clickable_streams(
        req, bbox="-78.0,40.6,-77.3,41.0", zoom=9))
    assert len(json.loads(resp2.body)["features"]) == 2
