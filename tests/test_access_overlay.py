"""The national access overlay supersedes the curated baselines when present,
and falls back cleanly when it isn't. Resets the module-level overlay cache
around each test so ordering never leaks into other files' access tests."""
import gzip
import json

import pytest

import access_points as ap
from states import point_in_state


@pytest.fixture(autouse=True)
def _reset_overlay():
    ap._overlay_loaded = False
    ap._overlay_by_state = None
    ap._access_cache.clear()
    yield
    ap._overlay_loaded = False
    ap._overlay_by_state = None
    ap._access_cache.clear()


def _write_overlay(tmp_path, pts):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
         "properties": {"name": p["name"], "type": p["type"],
                        "access": "public", "source": "osm"}}
        for p in pts]}
    f = tmp_path / "access.geojson.gz"
    with gzip.open(f, "wt", encoding="utf-8") as fh:
        json.dump(fc, fh)
    return str(f)


def test_overlay_supersedes_baseline_and_groups_by_state(tmp_path, monkeypatch):
    pts = [
        {"lat": 39.45, "lon": -76.62, "name": "MD River Ramp", "type": "boat_ramp"},
        {"lat": 37.50, "lon": -78.50, "name": "VA Access", "type": "walk_in"},
    ]
    path = _write_overlay(tmp_path, pts)
    monkeypatch.setattr(ap.data_source, "resolve_data_file",
                        lambda local, fn: path)
    for p in pts:
        st = point_in_state(p["lat"], p["lon"])
        assert st, f"{p['name']} is not inside any state bbox"
        got = ap.load_access_points(st)
        # the overlay point is served (source 'osm'), NOT the curated baseline
        assert any(g["name"] == p["name"] and g["source"] == "osm" for g in got)


def test_falls_back_to_baseline_without_overlay(monkeypatch):
    # No overlay file -> resolve returns a nonexistent path -> baseline path.
    monkeypatch.setattr(ap.data_source, "resolve_data_file",
                        lambda local, fn: "/nonexistent/access.geojson.gz")
    # keep the fallback network-free: live feeds return nothing -> baseline only
    monkeypatch.setattr(ap, "fetch_geojson_features", lambda url: None)
    md = ap.load_access_points("MD")
    assert md, "MD has a curated baseline to fall back to"
    assert any(p.get("source") == "baseline" for p in md)
