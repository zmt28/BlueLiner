"""Behavior-preservation tests for the declarative trout-source registry.

Phase 1 replaced the per-state fetch_trout_* functions with
data/trout/sources.json + trout_registry.row_bucket. These tests assert the
registry engine reproduces the OLD per-state classification *exactly*, using the
shipped 10-state values as the oracle (the build's _nc_bucket / NY MGMTCAT map /
GA flag logic). Pure + offline -- no network, no geopandas.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import trout_registry as reg  # noqa: E402

ALL_SOURCES = reg.load_sources()
SOURCES = {s["state"]: s for s in ALL_SOURCES}            # last wins per state
BY_LABEL = {s.get("label", s["state"]): s for s in ALL_SOURCES}


def test_registry_covers_states_with_valid_modes():
    assert {"MD", "VA", "PA", "NJ", "VT", "MA", "WV", "NY", "NC", "GA", "CT"} \
        <= {s["state"] for s in ALL_SOURCES}
    for s in ALL_SOURCES:
        assert s["mode"] in {"single", "multi_layer", "field_map",
                             "field_prefix", "flags"}
        if s["mode"] == "single":
            assert s["class"] and s.get("url")
        if s["mode"] == "multi_layer":
            assert s.get("base") and all(l.get("id") is not None and l.get("class")
                                         for l in s["layers"])


def test_single_bucket_states():
    assert reg.row_bucket(SOURCES["MD"], {}) == "designated"
    assert reg.row_bucket(SOURCES["VA"], {}) == "wild_reproduction"
    assert reg.row_bucket(SOURCES["WV"], {}) == "stocked"
    assert reg.row_bucket(SOURCES["NJ"], {}) == "stocked"


def test_ny_field_map_matches_old_mgmtcat_logic():
    ny = SOURCES["NY"]
    expect = {"Stocked": "stocked", "Stocked-Extended": "stocked",
              "Wild-Quality": "wild_reproduction", "Wild-Premier": "wild_reproduction",
              "Other": "wild_reproduction"}
    for value, want in expect.items():
        assert reg.row_bucket(ny, {"MGMTCAT": value}) == want
    # Unmapped value and missing field both drop (None), as the old .map did.
    assert reg.row_bucket(ny, {"MGMTCAT": "Heritage"}) is None
    assert reg.row_bucket(ny, {}) is None


def test_nc_field_prefix_matches_old_nc_bucket():
    nc = SOURCES["NC"]
    cases = {
        "Wild Trout Waters": "wild_reproduction",
        "Catch and Release/Artificial Flies and Lures Only Trout Waters": "wild_reproduction",
        "Special Regulation Trout Waters": "wild_reproduction",
        "Hatchery Supported Trout Waters": "stocked",
        "Delayed Harvest Trout Waters": "stocked",
        # Hurricane-Helene suffixed variants still bucket on the prefix:
        "Hatchery Supported Trout Waters - CLOSED UNTIL FURTHER NOTICE": "stocked",
        "Delayed Harvest Trout Waters - CLOSED UNTIL FURTHER NOTICE": "stocked",
    }
    for value, want in cases.items():
        assert reg.row_bucket(nc, {"FIRST_WRC_": value}) == want
    # Unmatched -> dropped.
    assert reg.row_bucket(nc, {"FIRST_WRC_": "Trout Pond"}) is None
    assert reg.row_bucket(nc, {"FIRST_WRC_": "", "WRC_Class": None}) is None
    # Coalesce: a null in the first field falls through to the second.
    assert reg.row_bucket(nc, {"FIRST_WRC_": None,
                               "WRC_Class": "Wild Trout Waters"}) == "wild_reproduction"


def test_ga_flags_match_old_mask_logic():
    ga = SOURCES["GA"]
    assert reg.row_bucket(ga, {"Hvy_stock": "Yes", "Delay_har": "No"}) == "stocked"
    assert reg.row_bucket(ga, {"Hvy_stock": "No", "Delay_har": "Yes"}) == "stocked"
    assert reg.row_bucket(ga, {"Hvy_stock": "No", "Delay_har": "No"}) == "wild_reproduction"
    assert reg.row_bucket(ga, {"Hvy_stock": " ", "Delay_har": " "}) == "wild_reproduction"
    assert reg.row_bucket(ga, {}) == "wild_reproduction"          # default, all tagged


def test_pa_multi_layer_classes():
    pa = SOURCES["PA"]
    classes = {l["class"] for l in pa["layers"]}
    assert classes == {"wild_reproduction", "class_a", "wilderness", "stocked"}


def test_field_prefix_default_bucket():
    # CT FMA: only "(Class 1)" WTMAs are wild; everything else -> default stocked.
    ct = BY_LABEL["CT (WTMA)"]
    f = ct["fields"][0]
    assert reg.row_bucket(ct, {f: "Heather Reaves Wild Trout Management Area (Class 1)"}) \
        == "wild_reproduction"
    assert reg.row_bucket(ct, {f: "Heather Reaves Wild Trout Management Area (Class 2)"}) \
        == "stocked"
    assert reg.row_bucket(ct, {f: "Trophy Trout Lake"}) == "stocked"   # default


def test_field_prefix_without_default_still_drops():
    # NC has no default -> unmatched rows stay None (dropped), unchanged.
    assert reg.row_bucket(SOURCES["NC"], {"FIRST_WRC_": "Trout Pond"}) is None


def test_wi_class_i_wild_ii_and_iii_stocked():
    # WI Class I = self-sustaining wild; Class II (stocked-supplemented) and
    # Class III (no reproduction) both -> stocked. The ordered substring rules
    # must not let "class i" leak into the Class II/III rows.
    wi = SOURCES["WI"]
    f = wi["fields"][0]
    assert reg.row_bucket(wi, {f: "Class I"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "Class II"}) == "stocked"
    assert reg.row_bucket(wi, {f: "Class III"}) == "stocked"
    # Case-insensitive + tolerant of the rendered-label vs stored-code casing.
    assert reg.row_bucket(wi, {f: "CLASS I"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "CLASS III"}) == "stocked"
    # Arabic fallback, in case the code isn't roman.
    assert reg.row_bucket(wi, {f: "Class 1"}) == "wild_reproduction"
    assert reg.row_bucket(wi, {f: "Class 2"}) == "stocked"
    # Unclassified / missing -> dropped (no default).
    assert reg.row_bucket(wi, {f: ""}) is None
    assert reg.row_bucket(wi, {}) is None


def test_ct_is_two_ordered_sources_wild_first():
    ct = [s for s in ALL_SOURCES if s["state"] == "CT"]
    assert [s["label"] for s in ct] == ["CT (WTMA)", "CT (stocked)"]  # wild claims first
    assert ct[0]["mode"] == "field_prefix" and ct[0]["default"] == "stocked"
    assert ct[1]["mode"] == "single" and ct[1]["class"] == "stocked"
