"""Unit tests for the live-feed registries and the per-source field
mapping in stocking.py (runtime) and the river-POI builder's access-feed
normalization (scripts/build_river_poi.py -- the access feeds are now a
build-time input to the national overlay, not a request-time fetch). All
synthetic features -- no network. Endpoint liveness is covered separately by
scripts/verify_feed_sources.py (run where egress is open)."""

import json
import os
import sys

import stocking

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "scripts"))
import build_river_poi as bp  # noqa: E402

_ACCESS_SOURCES_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "data", "access_points", "sources.json")


def _access_sources_by_state() -> dict[str, list[dict]]:
    """The access live-feed registry, grouped by state (the builder reads
    this file directly via fetch_agency_access)."""
    raw = json.load(open(_ACCESS_SOURCES_JSON)).get("sources", [])
    by_state: dict[str, list[dict]] = {}
    for src in raw:
        st = src.get("state")
        if st and src.get("url"):
            by_state.setdefault(st, []).append(src)
    return by_state


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
    by_state = _access_sources_by_state()
    for st in ("VA", "PA", "MD"):
        assert by_state.get(st), f"{st} missing live feed"
        for src in by_state[st]:
            assert "/query" in src["url"]
            assert src["agency_url"].startswith("https://")


def test_stocking_baselines_autodiscovered_from_data_dir():
    # The classic four-state seed must keep loading without the old
    # hardcoded state tuple. (Access baselines are retired -- the national
    # overlay is the sole source there.)
    assert {"MD", "VA", "WV", "PA"} <= set(stocking.STOCKING_BASELINE)
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
# The access feeds feed the national overlay at build time; their per-source
# field mapping (type_flags / fixed_type / type_field / dedupe / notes) is
# tested against build_river_poi.normalize_agency in test_build_river_poi.py.
# Here we just sanity-check that every registered source's declared mapping is
# coherent enough for the builder to consume.

def test_access_sources_field_mappings_are_coherent():
    valid_types = {"boat_ramp", "walk_in", "wading_access", "pier", "parking"}
    for src in json.load(open(_ACCESS_SOURCES_JSON)).get("sources", []):
        st = src.get("state", "?")
        assert src.get("name_field"), f"{st}: source missing name_field"
        if "fixed_type" in src:
            assert src["fixed_type"] in valid_types, \
                f"{st}: bad fixed_type {src['fixed_type']}"
        for t in (src.get("type_flags") or {}).values():
            assert t in valid_types, f"{st}: bad type_flags target {t}"
