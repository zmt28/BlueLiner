"""Pure unit tests (no network, no app lifespan) for the core logic."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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

def test_db_crud_and_owner_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    db.init_db()  # idempotent: re-running migration must not error
    assert db.healthcheck() is True

    alice, bob = "owner-alice", "owner-bob"
    assert db.list_pins(alice) == []
    p = db.add_pin(39.0, -77.0, "gravel lot", alice)
    assert p["id"] and p["note"] == "gravel lot"

    assert len(db.list_pins(alice)) == 1
    assert db.list_pins(bob) == []                 # bob can't see alice's pin
    assert db.delete_pin(p["id"], bob) is False    # bob can't delete it
    assert db.delete_pin(999, alice) is False
    assert db.delete_pin(p["id"], alice) is True   # owner can
    assert db.list_pins(alice) == []


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

def test_db_backend_selection_and_paramstyle(monkeypatch):
    assert db._IS_PG is False
    assert db._ph("WHERE id = ?") == "WHERE id = ?"
    monkeypatch.setattr(db, "_IS_PG", True)
    assert db._ph("WHERE id = ? AND lat = ?") == "WHERE id = %s AND lat = %s"


def test_states_national_and_resolve():
    from states import STATES
    assert len(STATES) >= 51  # 50 states + DC
    for code, info in STATES.items():
        assert len(code) == 2 and code.isupper()
        assert info["usgs_code"] == code.lower()
        assert len(info["center"]) == 2
    assert main._resolve_states("co") == ["CO"]
    assert main._resolve_states("WV") == ["WV"]
    assert main._resolve_states("all") is None   # no nationwide union
    assert main._resolve_states("ZZ") is None


def _fake_request(ip="9.9.9.9"):
    return SimpleNamespace(headers={}, client=SimpleNamespace(host=ip))


def test_pin_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "_PIN_RATE_MAX", 3)
    monkeypatch.setattr(main, "_pin_hits", {})
    req = _fake_request("203.0.113.7")
    for _ in range(3):
        main._rate_limit_pins(req)          # within limit -> no raise
    with pytest.raises(HTTPException) as ei:
        main._rate_limit_pins(req)          # 4th in window -> 429
    assert ei.value.status_code == 429
    assert main._rate_limit_pins(_fake_request("198.51.100.1")) is None  # other IP ok


def test_owner_from_device_token():
    tok = "550e8400-e29b-41d4-a716-446655440000"
    o1 = main._owner(SimpleNamespace(headers={"x-device-token": tok}))
    o2 = main._owner(SimpleNamespace(headers={"x-device-token": tok}))
    other = main._owner(SimpleNamespace(headers={"x-device-token": "different-token"}))
    assert o1 == o2 and len(o1) == 64 and o1 != other      # stable, hashed
    assert main._owner(SimpleNamespace(headers={}), required=False) is None
    with pytest.raises(HTTPException) as ei:
        main._owner(SimpleNamespace(headers={}), required=True)
    assert ei.value.status_code == 400


class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_arcgis_keyset_pagination(monkeypatch):
    """OBJECTID keyset paging fetches every row across pages, then stops."""
    import re as _re
    import arcgis

    rows = [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [-77.0, 39.0 + i / 100]},
         "properties": {"OBJECTID": i}}
        for i in range(1, 6)  # OBJECTID 1..5
    ]
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls["n"] += 1
            params = params or {}
            if params.get("f") == "json":
                return _FakeResp({"objectIdField": "OBJECTID"})
            m = _re.search(r">\s*(-?\d+)", params.get("where", ""))
            bound = int(m.group(1)) if m else -1
            n = int(params.get("resultRecordCount", 2))
            page = [r for r in rows if r["properties"]["OBJECTID"] > bound][:n]
            return _FakeResp({"type": "FeatureCollection", "features": page})

    monkeypatch.setattr(arcgis.httpx, "Client", FakeClient)
    gdf = arcgis.fetch_geojson_gdf(
        "https://x/y/MapServer/0/query?where=1=1", page_size=2)
    assert gdf is not None and len(gdf) == 5      # full coverage, no dupes
    assert calls["n"] == 4                         # 1 metadata + pages 2+2+1


def test_arcgis_keyset_no_progress_guard(monkeypatch):
    """A server that ignores the keyset where-clause must not loop forever."""
    import arcgis
    feat = {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-77.0, 39.0]},
            "properties": {"OBJECTID": 7}}
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls["n"] += 1
            if (params or {}).get("f") == "json":
                return _FakeResp({"objectIdField": "OBJECTID"})
            return _FakeResp({"type": "FeatureCollection",
                              "features": [feat, feat]})

    monkeypatch.setattr(arcgis.httpx, "Client", FakeClient)
    gdf = arcgis.fetch_geojson_gdf(
        "https://x/y/MapServer/0/query?where=1=1", page_size=2)
    assert gdf is not None and len(gdf) == 2   # one page kept
    assert calls["n"] == 3                      # metadata + page0 + page1(stop)


def test_nldi_flowline_merges_and_caches(monkeypatch):
    main._river_geom_cache.clear()
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls["n"] += 1
            seg = {"type": "Feature",
                   "geometry": {"type": "LineString",
                                "coordinates": [[-77, 39], [-77.1, 39.1]]},
                   "properties": {}}
            return _FakeResp({"type": "FeatureCollection", "features": [seg]})

    monkeypatch.setattr(main.httpx, "Client", FakeClient)
    fc = main._nldi_flowline("01589000")
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 2  # UM+DM
    assert calls["n"] == 2
    fc2 = main._nldi_flowline("01589000")           # cached -> no new calls
    assert calls["n"] == 2 and fc2 is fc


def test_nldi_flowline_graceful(monkeypatch):
    main._river_geom_cache.clear()

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): raise RuntimeError("nldi down")

    monkeypatch.setattr(main.httpx, "Client", Boom)
    assert main._nldi_flowline("99999999") == {"type": "FeatureCollection",
                                               "features": []}


def test_river_key():
    assert main._river_key("Gunpowder falls near glencoe, md")[1] == "Gunpowder Falls"
    assert main._river_key("Patapsco river near halethorpe, md")[1] == "Patapsco River"
    assert main._river_key(
        "North branch potomac river at barnum, wv")[1] == "North Branch Potomac River"
    assert main._river_key("Mossy creek, va")[1] == "Mossy Creek"
    k1, _ = main._river_key("GUNPOWDER FALLS NEAR GLENCOE, MD")
    k2, _ = main._river_key("Gunpowder falls at falls rd, md")
    assert k1 == k2 == "gunpowder falls"       # stable grouping key


def test_build_river_popup_html():
    z = hatches.zone_for(39.6361, -76.6889)
    river = {
        "name": "Gunpowder Falls", "lat": 39.6, "lon": -76.7,
        "overall": "green", "on_trout": True, "near_stocked": True,
        "hatch_zone": z, "active": hatches.active_hatches(z, 5), "month": 5,
        "stocked_waters": [{
            "water": "Gunpowder Falls (Falls Rd / Masemore)",
            "species": ["Brown", "Rainbow"], "category": "Tailwater",
            "season_months": (1, 12), "agency_url": "https://example.test"}],
        "gauges": [{
            "site_name": "Gunpowder falls near glencoe, md",
            "site_no": "01581920",
            "variables": [{"variable": "Streamflow", "value": "95",
                           "dateTime": "2026-05-17T08:00:00"}],
            "conditions": {"overall": "green", "temp": "green",
                           "flow": "green", "current_flow": 95.0},
            "historical_median": 80.0}],
    }
    html = main.build_river_popup_html(river)
    assert "Gunpowder Falls" in html
    assert "Hatching now" in html
    assert "Recently Stocked" in html                       # near_stocked chip
    assert "Trout Water" in html                            # on_trout chip
    assert "Stocked nearby" in html                         # stocked block
    assert "Gunpowder falls near glencoe, md" in html       # gauge sub-header
    assert 'data-site="01581920"' in html                   # trend button
    assert "Flow context" in html                           # median present
