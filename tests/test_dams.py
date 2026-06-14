"""Unit tests for dams.py NID normalization. All synthetic features --
no network. Endpoint liveness is covered by the gis-endpoint-verify
workflow (the sandbox egress allowlist blocks services2.arcgis.com)."""

import dams


def _feat(props, lon=-77.0, lat=39.0):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


def test_modern_nid_schema_normalizes():
    (p,) = dams._features_to_points([_feat({
        "NAME": "Liberty Dam",
        "RIVER_OR_STREAM": "Patapsco River",
        "PRIMARY_OWNER_TYPE": "Public Utility",
        "CITY": "Eldersburg",
        "NIDID": "MD00123",
        "PURPOSES": "Water Supply",
        "NID_HEIGHT": "170",
        "YEAR_COMPLETED": "1954",
    })])
    assert p["name"] == "Liberty Dam"
    assert p["river"] == "Patapsco River"
    assert p["owner"] == "Public Utility"
    assert p["city"] == "Eldersburg"
    assert p["nid_id"] == "MD00123"
    assert p["purposes"] == "Water Supply"
    assert p["height_ft"] == 170.0
    assert p["year"] == "1954"
    assert p["agency_url"] == dams.NID_AGENCY_URL


def test_legacy_nid_schema_field_names():
    # The legacy AGOL republishes use DAM_NAME / RIVER / OWNER_TYPE / DAM_HEIGHT.
    (p,) = dams._features_to_points([_feat({
        "DAM_NAME": "Cooper Lake",
        "RIVER": "Cooper Creek",
        "OWNER_TYPE": "Public Utility",
        "DAM_HEIGHT": "50.5",
    })])
    assert p["name"] == "Cooper Lake"
    assert p["river"] == "Cooper Creek"
    assert p["owner"] == "Public Utility"
    assert p["height_ft"] == 50.5


def test_none_string_treated_as_empty():
    # NID encodes blanks as the literal string "None".
    (p,) = dams._features_to_points([_feat({
        "NAME": "X Dam", "OTHER_NAMES": "None",
        "RIVER_OR_STREAM": "None", "NID_HEIGHT": "None"})])
    assert p["name"] == "X Dam"
    assert p["river"] is None
    assert p["height_ft"] is None


def test_missing_name_falls_back():
    (p,) = dams._features_to_points([_feat({"RIVER_OR_STREAM": "Foo Creek"})])
    assert p["name"] == "Dam"
    assert p["river"] == "Foo Creek"


def test_non_numeric_height_is_dropped():
    (p,) = dams._features_to_points([_feat({"NAME": "D", "DAM_HEIGHT": "n/a"})])
    assert p["height_ft"] is None


def test_empty_and_missing_geometry_skipped():
    feats = [
        _feat({"NAME": "Good"}),
        {"type": "Feature", "geometry": None, "properties": {"NAME": "NoGeom"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": []},
         "properties": {"NAME": "EmptyGeom"}},
    ]
    pts = dams._features_to_points(feats)
    assert [p["name"] for p in pts] == ["Good"]


def test_non_point_geometry_centroids():
    feat = {"type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": [[[-77.0, 39.0], [-77.0, 39.2],
                                          [-76.8, 39.2], [-76.8, 39.0],
                                          [-77.0, 39.0]]]},
            "properties": {"NAME": "Areal Dam"}}
    (p,) = dams._features_to_points([feat])
    assert abs(p["lon"] - -76.9) < 1e-6 and abs(p["lat"] - 39.1) < 1e-6


def test_dams_geojson_shape_from_cache(monkeypatch):
    # Seed the per-state cache to avoid the network fetch.
    dams._dams_cache["ZZ"] = [{
        "name": "Test Dam", "lat": 39.0, "lon": -77.0,
        "river": "Test River", "owner": "Federal", "city": "Nowhere",
        "purposes": "Recreation", "height_ft": 42.0, "year": "1960",
        "nid_id": "ZZ00001", "agency_url": dams.NID_AGENCY_URL,
    }]
    fc = dams.dams_geojson("ZZ")
    assert fc["type"] == "FeatureCollection"
    (f,) = fc["features"]
    assert f["geometry"] == {"type": "Point", "coordinates": [-77.0, 39.0]}
    # lat/lon are dropped from properties (they live in geometry).
    assert "lat" not in f["properties"] and "lon" not in f["properties"]
    assert f["properties"]["name"] == "Test Dam"
    assert f["properties"]["river"] == "Test River"
