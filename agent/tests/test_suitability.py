"""Unit tests for the deterministic coldwater-suitability scorer (no network)."""

from __future__ import annotations

from agent.suitability import coldwater_suitability

_FLOW3 = {"streamorder": 3, "lengthkm": 4.0}
_THERMAL_NONE = {"water_temp_f": None, "gauged": False}
_ACCESS_PUBLIC = {"access_ok": True, "access_tier": "public"}
_ACCESS_UNKNOWN = {"access_ok": False, "access_tier": "unknown"}
_ACCESS_PRIVATE = {"access_ok": False, "access_tier": "private_easement"}


def test_topology_dominates_and_proximity_ranks():
    near = coldwater_suitability(
        {"distance_mi": 0.2, "is_tributary_proxy": True, "same_named_as_trout": False,
         "nearest_trout": "X", "nearest_trout_class": "wild_reproduction"},
        _FLOW3, _THERMAL_NONE, _ACCESS_PUBLIC, mode="full")
    far = coldwater_suitability(
        {"distance_mi": 5.5, "is_tributary_proxy": False, "same_named_as_trout": False,
         "nearest_trout": "X", "nearest_trout_class": "wild_reproduction"},
        _FLOW3, _THERMAL_NONE, _ACCESS_PUBLIC, mode="full")
    assert near["suitability_score"] > far["suitability_score"]
    assert near["confidence"] > far["confidence"]


def test_same_named_as_trout_is_max_topology():
    s = coldwater_suitability(
        {"distance_mi": 3.0, "is_tributary_proxy": True, "same_named_as_trout": True,
         "nearest_trout": "Gunpowder", "nearest_trout_class": "wild_reproduction"},
        _FLOW3, _THERMAL_NONE, _ACCESS_PUBLIC, mode="full")
    assert s["components"]["topology"] == 1.0


def test_known_private_is_excluded():
    topo = {"distance_mi": 0.1, "is_tributary_proxy": True, "same_named_as_trout": True,
            "nearest_trout": "X", "nearest_trout_class": "class_a"}
    s = coldwater_suitability(topo, _FLOW3, _THERMAL_NONE, _ACCESS_PRIVATE, mode="full")
    assert s["suitability_score"] == 0.0
    assert any("private" in r for r in s["reasons"])


def test_unknown_access_flagged_not_excluded():
    topo = {"distance_mi": 0.1, "is_tributary_proxy": True, "same_named_as_trout": True,
            "nearest_trout": "X", "nearest_trout_class": "class_a"}
    s = coldwater_suitability(topo, _FLOW3, _THERMAL_NONE, _ACCESS_UNKNOWN, mode="full")
    assert s["suitability_score"] > 0.0          # NOT excluded
    assert s["needs_access_verify"] is True       # but flagged for verification


def test_size_is_a_plateau_not_more_is_better():
    # A small order-3 tributary should not score below a big order-5 river on size.
    topo = {"distance_mi": 1.0, "is_tributary_proxy": True, "same_named_as_trout": False,
            "nearest_trout": "X", "nearest_trout_class": "wild_reproduction"}
    small = coldwater_suitability(topo, {"streamorder": 3, "lengthkm": 4.0},
                                  _THERMAL_NONE, _ACCESS_PUBLIC, mode="full")
    big = coldwater_suitability(topo, {"streamorder": 5, "lengthkm": 20.0},
                                _THERMAL_NONE, _ACCESS_PUBLIC, mode="full")
    assert small["components"]["flow"] == big["components"]["flow"] == 1.0
