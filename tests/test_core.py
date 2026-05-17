"""Pure unit tests (no network, no app lifespan) for the core logic."""

import hatches
import stocking
import db
import main


# -- hatches --

def test_zone_for_gunpowder_is_limestone_tailwater():
    z = hatches.zone_for(39.6361, -76.6889)
    assert z["name"] == "Limestone & Tailwater"


def test_zone_for_regions_and_fallback():
    assert hatches.zone_for(38.51, -80.54)["name"] == "Mountain Freestone"
    assert hatches.zone_for(38.07, -77.5)["name"] == "Blue Ridge / Piedmont"
    assert hatches.zone_for(45.0, -120.0)["name"] == "Mid-Atlantic (general)"


def test_in_range_wraps_year_boundary():
    assert hatches._in_range(1, 10, 4)      # Jan inside Oct->Apr
    assert hatches._in_range(11, 10, 4)
    assert not hatches._in_range(6, 10, 4)
    assert hatches._in_range(5, 4, 6)
    assert not hatches._in_range(7, 4, 6)


def test_active_hatches_peak_first_and_nonempty():
    z = hatches.zone_for(39.6361, -76.6889)
    active = hatches.active_hatches(z, 5)
    assert active, "May should have active hatches"
    # entries currently peaking sort ahead of merely-active ones
    peaking = [hatches._in_range(5, e["peak"][0], e["peak"][1]) for e in active]
    assert peaking == sorted(peaking, reverse=True)


def test_all_insect_names_unique_sorted():
    names = hatches.all_insect_names()
    assert names == sorted(names)
    assert len(names) == len(set(names))


# -- stocking --

def test_baseline_includes_gunpowder():
    md = stocking.STOCKING_BASELINE["MD"]
    assert any("Gunpowder" in p["water"] for p in md)


def test_is_near_stocked_covers_reach_not_far():
    md = stocking.stocked_points("MD")  # MD has no live source -> pure
    assert stocking.is_near_stocked(39.566, -76.605, md)   # Glencoe gauge
    assert stocking.is_near_stocked(39.612, -76.729, md)   # Prettyboy outflow
    assert not stocking.is_near_stocked(39.283, -76.609, md)  # Inner Harbor


def test_stocked_points_wv_baseline_only():
    pts = stocking.stocked_points("WV")
    assert pts and all("water" in p for p in pts)


# -- db (temp file) --

def test_db_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    assert db.healthcheck() is True
    assert db.list_pins() == []
    p = db.add_pin(39.0, -77.0, "access via gravel lot")
    assert p["id"] and p["note"] == "access via gravel lot"
    assert len(db.list_pins()) == 1
    assert db.delete_pin(999) is False
    assert db.delete_pin(p["id"]) is True
    assert db.list_pins() == []


# -- scoring --

def test_score_conditions_cases():
    good = main.score_conditions(
        [{"variable": "Temperature, water, C", "value": "12"}], None)
    assert good["overall"] == "green"
    hot = main.score_conditions(
        [{"variable": "Temperature, water, C", "value": "26"}], None)
    assert hot["overall"] == "red"
    assert main.score_conditions([], None)["overall"] == "gray"


# -- popup --

def test_build_popup_html_sections():
    z = hatches.zone_for(39.6361, -76.6889)
    html = main.build_popup_html(
        "Gunpowder falls", [], {"overall": "green", "temp": "green",
        "flow": None, "current_flow": None}, None, on_trout=True,
        site_no="01581920", hatch_zone=z,
        active_hatches=hatches.active_hatches(z, 5),
        near_stocked=True, month=5)
    assert "Gunpowder falls" in html
    assert "Hatching now" in html
    assert "Recently Stocked" in html
    assert "Trout Water" in html
    assert 'data-site="01581920"' in html
