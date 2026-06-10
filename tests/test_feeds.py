"""Unit tests for the live-feed registries and the per-source field
mapping in stocking.py / access_points.py. All synthetic features --
no network. Endpoint liveness is covered separately by
scripts/verify_feed_sources.py (run where egress is open)."""

import access_points
import stocking


def _pt(props, lon=-77.5, lat=38.5):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": props}


# -- registries ---------------------------------------------------------

def test_stocking_registry_has_verified_states():
    for st in ("VA", "PA", "MD"):
        assert stocking.STOCKING_SOURCES.get(st), f"{st} missing live feed"
        for src in stocking.STOCKING_SOURCES[st]:
            assert "/query" in src["url"]
            assert src["agency_url"].startswith("https://")


def test_access_registry_has_verified_states():
    for st in ("VA", "PA", "MD"):
        assert access_points.ACCESS_SOURCES.get(st), f"{st} missing live feed"
        for src in access_points.ACCESS_SOURCES[st]:
            assert "/query" in src["url"]
            assert src["agency_url"].startswith("https://")


def test_baselines_autodiscovered_from_data_dir():
    # The classic four-state seed must keep loading without the old
    # hardcoded state tuple.
    assert {"MD", "VA", "WV", "PA"} <= set(stocking.STOCKING_BASELINE)
    assert {"MD", "VA", "WV", "PA"} <= set(access_points.ACCESS_BASELINE)
    # sources.json must not leak in as a fake state.
    assert "SOURCE" not in stocking.STOCKING_BASELINE
    assert all(len(s) == 2 for s in stocking.STOCKING_BASELINE)


# -- stocking field mapping ---------------------------------------------

def test_species_flags_mapping():
    src = {"agency_url": "https://x.gov", "category": "Stocked",
           "species_flags": {"RainbowTrout": "Rainbow",
                             "BrownTrout": "Brown",
                             "BrookTrout": "Brook"}}
    feats = [_pt({"Waterbody": "Beartree Lake",
                  "RainbowTrout": 1, "BrownTrout": 0, "BrookTrout": 1})]
    src["name_field"] = "Waterbody"
    (p,) = stocking._features_to_points(feats, src)
    assert p["water"] == "Beartree Lake"
    assert p["species"] == ["Rainbow", "Brook"]


def test_species_field_splits_combined_strings():
    src = {"agency_url": "https://x.gov", "name_field": "LOCATION",
           "species_field": "Species", "category": "Stocked"}
    (p,) = stocking._features_to_points(
        [_pt({"LOCATION": "Mill Run", "Species": "Rainbow/Golden"})], src)
    assert p["species"] == ["Rainbow", "Golden"]


def test_source_season_months_override():
    src = {"agency_url": "https://x.gov", "name_field": "W",
           "season_months": [10, 5], "category": "Stocked"}
    (p,) = stocking._features_to_points([_pt({"W": "X Creek"})], src)
    assert p["season_months"] == (10, 5)


def test_dedupe_collapses_same_water_nearby_segments():
    src = {"agency_url": "https://x.gov", "name_field": "W",
           "dedupe": True, "category": "Stocked"}
    feats = [
        _pt({"W": "Wills Creek"}, lon=-78.751, lat=39.831),
        _pt({"W": "Wills Creek"}, lon=-78.752, lat=39.832),   # same cell
        _pt({"W": "Wills Creek"}, lon=-78.30, lat=39.50),     # far segment
        _pt({"W": "Other Run"},   lon=-78.751, lat=39.831),   # other water
    ]
    pts = stocking._features_to_points(feats, src)
    names = [(p["water"], round(p["lat"], 1)) for p in pts]
    assert len(pts) == 3
    assert names.count(("Wills Creek", 39.8)) == 1


# -- access field mapping ------------------------------------------------

def test_access_type_flags_first_truthy_wins():
    src = {"agency_url": "https://x.gov", "name_field": "N",
           "type_flags": {"RAMP": "boat_ramp", "PIER": "pier",
                          "SHORE_FISH": "walk_in"}}
    feats = [
        _pt({"N": "Ramp+Pier", "RAMP": "Y", "PIER": "Y"}),
        _pt({"N": "Pier only", "RAMP": "N", "PIER": "Y"}),
        _pt({"N": "Nothing", "RAMP": "N", "PIER": "N"}),
    ]
    pts = access_points._features_to_points(feats, src)
    assert [p["type"] for p in pts] == ["boat_ramp", "pier", "walk_in"]


def test_access_fixed_type_and_notes_field():
    src = {"agency_url": "https://x.gov", "name_field": "Facility_N",
           "fixed_type": "pier", "notes_field": "Body_of_Wa"}
    (p,) = access_points._features_to_points(
        [_pt({"Facility_N": "Cameron Run", "Body_of_Wa": "Lake Cook"})], src)
    assert p["type"] == "pier"
    assert p["notes"] == "Lake Cook"
    assert p["access"] == "public"


def test_access_type_normalizer_boat_keyword():
    # KY publishes AccessType values like "Any Boat" / "Small Boat Only"
    assert access_points._normalize_type("Any Boat") == "boat_ramp"
    assert access_points._normalize_type("Small Boat Only") == "boat_ramp"
    assert access_points._normalize_type("Bank Access") == "walk_in"


def test_access_dedupe_collapses_parcels():
    """NY PFR publishes thousands of tiny bank parcels per stream; dedupe
    keeps one pin per named water per ~1 km cell."""
    src = {"agency_url": "https://x.gov", "name_field": "W",
           "fixed_type": "walk_in", "dedupe": True}
    feats = [
        _pt({"W": "Beaver Kill"}, lon=-74.9012, lat=41.9501),
        _pt({"W": "Beaver Kill"}, lon=-74.9014, lat=41.9503),  # same cell
        _pt({"W": "Beaver Kill"}, lon=-74.93, lat=41.96),      # next cell
    ]
    pts = access_points._features_to_points(feats, src)
    assert len(pts) == 2


def test_access_centroids_non_point_geometry():
    src = {"agency_url": "https://x.gov", "name_field": "N"}
    feat = {"type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[-77.0, 38.0], [-77.2, 38.2]]},
            "properties": {"N": "Reach"}}
    (p,) = access_points._features_to_points([feat], src)
    assert abs(p["lon"] - -77.1) < 1e-6 and abs(p["lat"] - 38.1) < 1e-6
