"""River-level trout-chip derivation (W3 of the trout coverage plan).

The panel header must describe the RIVER, not the clicked pixel: any
flowline sharing the clicked reach's levelpath group (or its normalized
name when no levelpath evidence exists) that carries a trout_class lights
the "Trout water" chip, and the strongest class wins the label. All
synthetic data -- no network, no real bundle scan.
"""

import gzip
import json

import hatches
import main
import reach_trout


def _bundle(tmp_path, props_list):
    """Write a synthetic clickable-streams geojson.gz from properties."""
    path = str(tmp_path / "streams.geojson.gz")
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
         "properties": p}
        for p in props_list]}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(fc, f)
    return path


def test_build_index_strongest_class_per_group(tmp_path):
    path = _bundle(tmp_path, [
        # Fall Creek symptom: most flowlines untagged, a few tagged.
        {"comid": 1, "levelpathid": 100, "gnis_name": "Fall Creek",
         "streamorder": 3, "lengthkm": 2.0, "trout_class": None},
        {"comid": 2, "levelpathid": 100, "gnis_name": "Fall Creek",
         "streamorder": 3, "lengthkm": 1.0, "trout_class": "wild_reproduction"},
        # Weaker class on the same group never downgrades the strongest.
        {"comid": 3, "levelpathid": 100, "gnis_name": "FALL CREEK",
         "streamorder": 4, "lengthkm": 1.0, "trout_class": "stocked"},
        # "nan" names (stringified pandas NaN) must not enter the name index.
        {"comid": 4, "levelpathid": 200, "gnis_name": "nan",
         "streamorder": 2, "lengthkm": 1.0, "trout_class": "class_a"},
        # Untagged + unnamed contributes nothing.
        {"comid": 5, "levelpathid": 300, "gnis_name": None,
         "streamorder": 1, "lengthkm": 1.0, "trout_class": None},
    ])
    by_lpid, by_name = reach_trout.build_index(path)
    assert by_lpid == {100: "wild_reproduction", 200: "class_a"}
    assert by_name == {"fall creek": "wild_reproduction"}


def test_build_index_missing_file(tmp_path):
    by_lpid, by_name = reach_trout.build_index(str(tmp_path / "nope.gz"))
    assert by_lpid == {} and by_name == {}


def _fake_index(monkeypatch):
    monkeypatch.setattr(reach_trout, "_index", (
        {100: "wild_reproduction", 200: "class_a"},
        {"fall creek": "wild_reproduction"},
    ))


def test_river_trout_class_levelpath_group(monkeypatch):
    _fake_index(monkeypatch)
    # Any flowline in the levelpath group lights the chip -- the clicked
    # flowline itself being untagged doesn't matter.
    assert reach_trout.river_trout_class([100], None) == "wild_reproduction"
    # Strongest class across the river's levelpath group wins.
    assert reach_trout.river_trout_class([100, 200], None) == "class_a"
    # Name fallback only when the levelpath group has no evidence.
    assert reach_trout.river_trout_class([], "Fall Creek") == "wild_reproduction"
    assert reach_trout.river_trout_class([999], "Fall Creek") == "wild_reproduction"
    assert reach_trout.river_trout_class(None, "FALL CREEK ") == "wild_reproduction"
    # No evidence at all -> no chip.
    assert reach_trout.river_trout_class([999], "Unknown Run") is None
    assert reach_trout.river_trout_class(None, None) is None
    assert reach_trout.river_trout_class(None, "nan") is None


def test_reach_detail_payload_river_trout_block(monkeypatch):
    _fake_index(monkeypatch)
    # Ocean point -> no state lookups; the trout block still answers for
    # the levelpath group, so the ungauged card matches the gauged panel.
    p = main._reach_detail_payload(10.0, -30.0, None, levelpathid=100)
    assert p["trout"] == {"river_class": "wild_reproduction",
                          "river_label": "Wild reproduction"}
    # Name fallback when the clicked reach carries no levelpathid.
    p = main._reach_detail_payload(10.0, -30.0, "Fall Creek", levelpathid=None)
    assert p["trout"]["river_class"] == "wild_reproduction"
    # No evidence -> explicit nulls (stable keys for the card renderer).
    p = main._reach_detail_payload(10.0, -30.0, "Unknown Run", levelpathid=999)
    assert p["trout"] == {"river_class": None, "river_label": None}


def _river(**overrides):
    z = hatches.zone_for(39.6361, -76.6889)
    river = {
        "name": "Fall Creek", "lat": 42.45, "lon": -76.47,
        "overall": "green", "on_trout": True, "trout_class": None,
        "near_stocked": False, "hatch_zone": z, "active": [], "month": 5,
        "stocked_waters": [], "access_count": 0,
        "gauges": [{
            "site_name": "Fall creek near ithaca, ny", "site_no": "04234000",
            "variables": [{"variable": "Streamflow", "value": "120",
                           "dateTime": "2026-06-01T08:00:00"}],
            "conditions": {"overall": "green", "current_flow": 120.0},
            "historical_median": 110.0}],
    }
    river.update(overrides)
    return river


def test_panel_trout_chip_labels_strongest_class():
    # River-level class drives the chip label (strongest class wins).
    html = main.build_river_popup_html(_river(trout_class="class_a"))
    assert "Trout water &middot; Class A wild" in html
    # Proximity-only evidence (no classed flowline) -> plain chip.
    html = main.build_river_popup_html(_river(trout_class=None))
    assert "Trout water<" in html and "&middot; Class A wild" not in html
    # No trout signal at all -> no chip.
    html = main.build_river_popup_html(_river(on_trout=False))
    assert "pill--trout" not in html


def test_panel_proximity_chips_say_nearby():
    # Access renders from the on-map PMTiles layer now, so the panel no longer
    # shows an access chip or an "Access nearby" stat (the overlay that fed the
    # count was retired to free the 512 MB app process).
    html = main.build_river_popup_html(_river())
    assert "access point" not in html
    assert "Access nearby" not in html
    html = main.build_river_popup_html(_river(near_stocked=True))
    assert "Stocked water nearby" in html
    assert "Recently stocked" not in html
