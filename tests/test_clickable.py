"""Phase B backend: clickable-stream network bulk-load. Offline -- a tiny
synthetic GeoJSON fixture, no network.

The viewport/tier query (db.query_clickable_streams) + the
/api/clickable_streams endpoint were retired when the network moved to
static vector tiles (M3); the loader + the bundled data file remain (the
tiles are built from that file), so this keeps the load coverage."""

import gzip
import json

import db


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
