"""Unit tests for the river-POI builder's pure + shapely core
(scripts/build_river_poi.py). Source fetching, EPSG:5070 projection, and the
geopandas stack run only in the data-build job; here we cover normalization,
cross-source dedupe, and the STRtree clip/associate with synthetic planar
coordinates (shapely only, no geopandas/pyproj)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import build_river_poi as bp  # noqa: E402


def test_normalize_osm_types_and_ids():
    pts = bp.normalize_osm([
        {"type": "node", "id": 1, "lat": 39.5, "lon": -76.6,
         "tags": {"leisure": "slipway", "name": "Ramp A"}},
        {"type": "way", "id": 2, "center": {"lat": 39.6, "lon": -76.7},
         "tags": {"leisure": "fishing", "name": "Spot B"}},
        {"type": "node", "id": 3, "lat": 39.7, "lon": -76.8,
         "tags": {"leisure": "fishing", "access": "private"}},
        {"type": "node", "id": 4, "tags": {"leisure": "slipway"}},  # no coords
    ])
    assert len(pts) == 3                                  # coordless dropped
    by_id = {p["source_id"]: p for p in pts}
    assert by_id["n1"]["type"] == "boat_ramp" and by_id["n1"]["access"] == "public"
    assert by_id["w2"]["type"] == "wading_access"         # way center used
    assert by_id["n3"]["access"] == "private"
    assert all(p["source"] == "osm" for p in pts)


def test_normalize_ridb_drops_null_island_and_types_launches():
    pts = bp.normalize_ridb([
        {"FacilityID": 10, "FacilityName": "Smith Boat Launch",
         "FacilityLatitude": 40.0, "FacilityLongitude": -77.0},
        {"FacilityID": 11, "FacilityName": "River Fishing Site",
         "FacilityLatitude": 0, "FacilityLongitude": 0},     # null island -> drop
        {"FacilityID": 12, "FacilityName": "Quiet Trailhead",
         "FacilityLatitude": 40.1, "FacilityLongitude": -77.1},
    ])
    assert {p["source_id"]: p["type"] for p in pts} == {
        "10": "boat_ramp", "12": "walk_in"}


def test_dedupe_prefers_authoritative_source():
    pts = [
        {"lat": 39.50000, "lon": -76.60000, "type": "boat_ramp", "source": "osm"},
        {"lat": 39.50005, "lon": -76.60000, "type": "boat_ramp", "source": "agency"},
        {"lat": 39.70000, "lon": -76.60000, "type": "boat_ramp", "source": "osm"},
        {"lat": 39.50000, "lon": -76.60000, "type": "pier", "source": "osm"},
    ]
    out = bp.dedupe(pts)
    ramps = [p for p in out if p["type"] == "boat_ramp"]
    # the two ~5 m-apart ramps collapse to ONE, keeping the agency coordinate;
    # the far ramp + the different-type pier at the same spot both survive.
    assert len(ramps) == 2
    near = min(ramps, key=lambda p: abs(p["lat"] - 39.5))
    assert near["source"] == "agency"
    assert any(p["type"] == "pier" for p in out)
    assert len(out) == 3


def test_clip_and_associate_keeps_near_drops_far():
    from shapely.geometry import LineString
    streams = [LineString([(0, 0), (1000, 0)])]   # planar metres (EPSG:5070-ish)
    lpids = [5000]
    pts = [
        (500.0, 30.0, {"name": "near", "type": "boat_ramp"}),   # 30 m off -> keep
        (500.0, 200.0, {"name": "far", "type": "walk_in"}),     # 200 m off -> drop
    ]
    kept = bp.clip_and_associate(pts, streams, lpids, buffer_m=75.0)
    assert [k["name"] for k in kept] == ["near"]
    assert kept[0]["levelpathid"] == 5000
