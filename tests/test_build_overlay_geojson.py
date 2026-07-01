"""Unit tests for the point-overlay GeoJSON producer's pure transform
(scripts/build_overlay_geojson.py). The per-state fetchers need egress (NID /
agency ArcGIS) and run only in the data-build job; here we cover the
dict->GeoJSON shaping that feeds the PMTiles build."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import build_overlay_geojson as bog  # noqa: E402


def test_to_feature_collection_folds_coords_and_keeps_props():
    fc = bog.to_feature_collection([
        {"lat": 39.5, "lon": -76.6, "name": "Prettyboy Dam",
         "river": "Gunpowder", "nid_id": "MD00123"},
        {"lat": 40.1, "lon": -77.2, "water": "Beaver Creek",
         "species": ["Rainbow", "Brown"], "category": "Stocked"},
    ])
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    f0 = fc["features"][0]
    assert f0["geometry"] == {"type": "Point", "coordinates": [-76.6, 39.5]}
    # lat/lon fold into geometry; every other key rides as a tile property.
    assert "lat" not in f0["properties"] and "lon" not in f0["properties"]
    assert f0["properties"]["name"] == "Prettyboy Dam"
    assert f0["properties"]["river"] == "Gunpowder"
    # list-valued props (stocking species) pass through untouched here; the
    # tile build serializes them and the client parses on read.
    assert fc["features"][1]["properties"]["species"] == ["Rainbow", "Brown"]


def test_to_feature_collection_skips_bad_coords():
    fc = bog.to_feature_collection([
        {"name": "no coords"},
        {"lat": None, "lon": -77.0, "name": "null lat"},
        {"lat": "nan-ish", "lon": -77.0, "name": "unparseable"},
        {"lat": 38.0, "lon": -77.0, "name": "good"},
    ])
    assert [f["properties"]["name"] for f in fc["features"]] == ["good"]


def test_layer_registry_and_defaults():
    assert set(bog.LAYERS) == {"dams", "stocking"}
    assert bog.DEFAULT_OUT["dams"].endswith("data/dams/dams.geojson.gz")
    assert bog.DEFAULT_OUT["stocking"].endswith(
        "data/stocking/stocking.geojson.gz")
