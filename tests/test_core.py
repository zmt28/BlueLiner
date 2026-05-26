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
    feats = arcgis.fetch_geojson_features(
        "https://x/y/MapServer/0/query?where=1=1", page_size=2)
    assert feats is not None and len(feats) == 5  # full coverage, no dupes
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
    feats = arcgis.fetch_geojson_features(
        "https://x/y/MapServer/0/query?where=1=1", page_size=2)
    assert feats is not None and len(feats) == 2   # one page kept
    assert calls["n"] == 3                      # metadata + page0 + page1(stop)


def test_nldi_flowline_merges_and_caches(monkeypatch):
    main._river_geom_cache.clear()
    monkeypatch.setattr(db, "get_river_geom", lambda s: None)
    monkeypatch.setattr(db, "put_river_geom", lambda s, fc: None)
    # No gnis_name -> conservative walk, no per-feature filter
    monkeypatch.setattr(main, "_nldi_gauge_meta", lambda s: {})
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
    assert fc["_walk_version"] == main._GEOM_SCHEMA_VERSION
    assert calls["n"] == 2
    fc2 = main._nldi_flowline("01589000")           # cached -> no new calls
    assert calls["n"] == 2 and fc2 is fc


def test_nldi_flowline_graceful(monkeypatch):
    main._river_geom_cache.clear()
    monkeypatch.setattr(db, "get_river_geom", lambda s: None)
    monkeypatch.setattr(db, "put_river_geom", lambda s, fc: None)

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): raise RuntimeError("nldi down")

    monkeypatch.setattr(main.httpx, "Client", Boom)
    assert main._nldi_flowline("99999999") == {"type": "FeatureCollection",
                                               "features": []}


def test_states_in_bbox():
    import states
    md_area = states.states_in_bbox(-79.0, 38.0, -76.0, 40.0)
    assert "MD" in md_area and "VA" in md_area
    assert "CA" not in md_area and "FL" not in md_area
    co_area = states.states_in_bbox(-106.0, 38.5, -104.5, 40.0)
    assert "CO" in co_area
    assert states.states_in_bbox(-30.0, 10.0, -29.0, 11.0) == []  # Atlantic ocean


def test_parse_bbox(monkeypatch):
    assert main._parse_bbox("-77,38,-76,39") == (-77.0, 38.0, -76.0, 39.0)
    for bad in ("1,2,3", "a,b,c,d", "-76,38,-77,39",      # short / nan / w>e
                "-76,40,-75,38", "-200,38,-76,39"):        # s>n / out of range
        with pytest.raises(HTTPException) as ei:
            main._parse_bbox(bad)
        assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:               # too large
        main._parse_bbox("-90,30,-80,40")
    assert ei.value.status_code == 400


def test_rivers_for_bbox_clips_and_dedupes(monkeypatch):
    import asyncio

    async def fake(st):
        return {
            "MD": [
                {"name": "In Box", "lat": 39.0, "lon": -76.7, "site_no": "1"},
                {"name": "Too North", "lat": 39.9, "lon": -76.7, "site_no": "2"},
            ],
            "VA": [
                {"name": "In Box", "lat": 39.0, "lon": -76.7, "site_no": "1"},  # dup
                {"name": "VA One", "lat": 38.9, "lon": -76.8, "site_no": "3"},
            ],
        }[st]

    monkeypatch.setattr(main, "_rivers_for_state_cached", fake)
    monkeypatch.setattr(main, "states_in_bbox", lambda *a: ["MD", "VA"])
    out = asyncio.run(main._rivers_for_bbox((-77.0, 38.8, -76.4, 39.2)))
    names = sorted(r["name"] for r in out)
    assert names == ["In Box", "VA One"]  # north one clipped, dup removed


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
    assert 'data-site="01581920"' in html                   # chart placeholder
    assert "Flow context" in html                           # median present
    # Redesign: collapsible sections, summary, chart placeholder, and the
    # catch CTA hoisted above the gauge sections.
    assert "<details" in html and "<summary>" in html
    assert 'class="bl-flow-chart"' in html
    assert "bl-summary" in html
    assert html.index("bl-catch-cta") < html.index("bl-gauge")  # CTA first


def test_score_conditions_returns_temp_f():
    out = main.score_conditions(
        [{"variable": "Temperature, water, C", "value": "15"}], None)
    assert out["temp_f"] == 59.0                            # 15C -> 59F
    assert main.score_conditions([], None)["temp_f"] is None


def test_ranking_summary_phrasing():
    def river(cur_flow, median, temp_c):
        variables = []
        cond = {"current_flow": cur_flow}
        if temp_c is not None:
            tf = round(temp_c * 9 / 5 + 32, 1)
            cond["temp_f"] = tf
        else:
            cond["temp_f"] = None
        return {"gauges": [{
            "site_no": "X", "variables": [{"variable": "Streamflow",
                                            "value": str(cur_flow or 0)}],
            "conditions": cond, "historical_median": median}]}

    # 60 vs 80 median -> 25% below; 13C -> 55.4F ideal
    s = main._ranking_summary_html(river(60.0, 80.0, 13))
    assert "25% below average" in s and "ideal" in s
    assert "for this time of year" in s        # time-bound comparison
    # 160 vs 80 -> 100% above; 21C -> 69.8F too warm
    s = main._ranking_summary_html(river(160.0, 80.0, 21))
    assert "100% above average" in s and "too warm" in s
    # within 15% -> near normal
    s = main._ranking_summary_html(river(85.0, 80.0, 10))
    assert "near normal" in s
    # no median -> raw cfs; no temp
    s = main._ranking_summary_html(river(42.0, None, None))
    assert "42 cfs" in s
    # nothing -> graceful
    s = main._ranking_summary_html(river(None, None, None))
    assert "Limited live data" in s


# -- bounded cache (memory: the OOM fix) --

def test_lru_ttl_evicts_by_size_and_ttl(monkeypatch):
    import cache
    clock = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock["t"])
    c = cache.LruTtl(maxsize=2, ttl=10.0)
    c["a"] = 1
    c["b"] = 2
    assert "a" in c and c.get("b") == 2
    c["c"] = 3                       # over maxsize -> evict LRU ("a")
    assert "a" not in c and len(c) == 2 and c.get("a") is None
    c.get("b")                       # touch b -> "c" becomes the LRU
    c["d"] = 4                       # evict "c", keep "b","d"
    assert "c" not in c and "b" in c and "d" in c
    clock["t"] += 11                 # every entry now older than ttl
    assert "b" not in c and c.get("d") is None and len(c) == 0


# -- durable cross-restart caches (speed: survive deploys) --

def test_db_river_geom_and_stats_roundtrip(tmp_path, monkeypatch):
    import datetime as _dt
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()

    fc = {"type": "FeatureCollection", "features": [1, 2]}
    assert db.get_river_geom("S1") is None
    db.put_river_geom("S1", fc)
    assert db.get_river_geom("S1") == fc
    db.put_river_geom("S1", {"type": "FeatureCollection", "features": [9]})
    assert db.get_river_geom("S1")["features"] == [9]      # upsert overwrites

    assert db.get_river_stats(["A", "B"]) == {}
    db.put_river_stats("A", {"5-19": 80.0})
    assert db.get_river_stats(["A", "B"]) == {"A": {"5-19": 80.0}}

    old = (_dt.datetime.now(_dt.timezone.utc)
           - _dt.timedelta(days=40)).isoformat()
    with db._conn() as conn:
        conn.cursor().execute(
            db._ph("UPDATE river_stats SET created_at = ? WHERE site_no = ?"),
            (old, "A"))
    assert db.get_river_stats(["A"]) == {}                 # >30d -> excluded


def test_nldi_flowline_uses_db_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._river_geom_cache.clear()
    saved = {"type": "FeatureCollection",
             "features": [{"type": "Feature",
                           "geometry": {"type": "LineString",
                                        "coordinates": [[-77, 39], [-77.1, 39.1]]},
                           "properties": {}}],
             # current schema version => DB hit serves directly
             "_walk_version": main._GEOM_SCHEMA_VERSION}
    db.put_river_geom("01581920", saved)

    class NoNet:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            raise AssertionError("DB hit should preempt the network")

    monkeypatch.setattr(main.httpx, "Client", NoNet)
    assert main._nldi_flowline("01581920") == saved       # straight from the DB


def test_nldi_flowline_refetches_stale_schema(tmp_path, monkeypatch):
    """A row written under an older walk/filter schema is treated as a
    cache-miss so logic changes propagate without operational cleanup."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._river_geom_cache.clear()
    monkeypatch.setattr(main, "_nldi_gauge_meta", lambda s: {})  # no filter
    # Pre-existing row with no _walk_version: stale by definition.
    db.put_river_geom("01589000", {"type": "FeatureCollection",
                                   "features": [{"old": True}]})

    refetched = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            refetched["n"] += 1
            return _FakeResp({"type": "FeatureCollection", "features": [{
                "type": "Feature",
                "geometry": {"type": "LineString",
                             "coordinates": [[-77, 39], [-77.1, 39.1]]},
                "properties": {}}]})

    monkeypatch.setattr(main.httpx, "Client", FakeClient)
    fc = main._nldi_flowline("01589000")
    assert refetched["n"] == 2                  # UM + DM refetched
    assert fc["_walk_version"] == main._GEOM_SCHEMA_VERSION


def test_nldi_flowline_filters_cross_river_segments(monkeypatch):
    """The Gunpowder/Georges-Run case: walk returns features from two
    rivers; per-COMID gnis filter keeps only the gauge's own river."""
    main._river_geom_cache.clear()
    main._comid_meta_cache.clear()
    monkeypatch.setattr(db, "get_river_geom", lambda s: None)
    monkeypatch.setattr(db, "put_river_geom", lambda s, fc: None)
    monkeypatch.setattr(
        main, "_nldi_gauge_meta",
        lambda s: {"comid": "111", "gnis_name": "Georges Run"})

    # Two NLDI flowline features: one ON Georges Run, one on Gunpowder.
    seg_own = {"type": "Feature", "properties": {"nhdplus_comid": "111"},
               "geometry": {"type": "LineString",
                            "coordinates": [[-76.69, 39.61], [-76.69, 39.60]]}}
    seg_other = {"type": "Feature", "properties": {"nhdplus_comid": "222"},
                 "geometry": {"type": "LineString",
                              "coordinates": [[-76.69, 39.55], [-76.7, 39.5]]}}

    def fake_walk(client, base, nav, dist):
        return [seg_own] if nav == "UM" else [seg_other]

    monkeypatch.setattr(main, "_fetch_nav", fake_walk)
    monkeypatch.setattr(
        main, "_comid_meta",
        lambda c: {"gnis_name": "Georges Run"} if c == "111"
                  else {"gnis_name": "Gunpowder Falls"})
    monkeypatch.setattr(main.httpx, "Client",
                        lambda *a, **k: _CtxNoop())

    fc = main._nldi_flowline("01580000")
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["nhdplus_comid"] == "111"


class _CtxNoop:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, *a, **k): raise AssertionError("fetcher monkeypatched")


def test_comid_meta_short_circuits_on_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._comid_meta_cache.clear()
    db.put_comid_meta("111", {"gnis_name": "Gunpowder Falls"})

    class NoNet:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            raise AssertionError("DB hit should preempt the NLDI call")

    monkeypatch.setattr(main.httpx, "Client", NoNet)
    assert main._comid_meta("111") == {"gnis_name": "Gunpowder Falls"}


# -- stats off the hot path (speed: no 20s stall) --

def test_ensure_medians_non_blocking(monkeypatch):
    import asyncio
    main._stats_cache.clear()
    scheduled = {}
    monkeypatch.setattr(db, "get_river_stats", lambda nos: {})  # nothing persisted
    monkeypatch.setattr(main, "_schedule_stats_warm",
                        lambda nos: scheduled.setdefault("nos", list(nos)))
    asyncio.run(main._ensure_medians_cached(["111", "222"]))
    assert scheduled["nos"] == ["111", "222"]   # missing -> background warm
    assert main._stats_cache.get("111") is None  # nothing fetched synchronously


def test_assemble_rivers_no_block_when_no_medians(monkeypatch):
    import asyncio
    calls = {}

    async def fake_ensure(nos):
        calls["nos"] = list(nos)   # records, leaves _stats_cache empty

    monkeypatch.setattr(main, "_ensure_medians_cached", fake_ensure)
    main._stats_cache.clear()
    ts = [{
        "sourceInfo": {
            "siteName": "Gunpowder Falls near Glencoe, MD",
            "siteCode": [{"value": "01581920"}],
            "geoLocation": {"geogLocation": {"latitude": 39.566,
                                             "longitude": -76.605}},
        },
        "variable": {"variableDescription": "Streamflow, ft3/s"},
        "values": [{"value": [{"value": "95",
                               "dateTime": "2026-05-19T08:00:00"}]}],
    }]
    rivers = asyncio.run(main._assemble_rivers(ts, [None], []))
    assert len(rivers) == 1 and rivers[0]["name"] == "Gunpowder Falls"
    assert calls["nos"] == ["01581920"]          # discharge -> medians requested
    assert rivers[0]["conditions"]["overall"] in ("green", "yellow", "red")


def test_rivers_for_bbox_caps_state_fanout(monkeypatch):
    import asyncio
    seen = []

    async def fake(st):
        seen.append(st)
        return []

    # 6 candidate states; only the 4 with the largest overlap should load.
    monkeypatch.setattr(main, "states_in_bbox",
                        lambda *a: ["MD", "VA", "WV", "PA", "DE", "NJ"])
    monkeypatch.setattr(main, "_rivers_for_state_cached", fake)
    asyncio.run(main._rivers_for_bbox((-79.0, 38.0, -76.0, 40.0)))
    assert len(seen) == main._BBOX_MAX_STATES == 4
    assert "MD" in seen                          # biggest overlap kept


# -- precompute + serve-from-Postgres (the instant-load architecture) --

def test_db_river_snapshot_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    assert db.get_river_snapshot("MD") is None
    rivers = [{"name": "Gunpowder Falls", "site_no": "01581920",
               "color": "#2ecc71"}]
    db.put_river_snapshot("MD", rivers)
    got = db.get_river_snapshot("MD")
    assert got is not None
    data, updated_at = got
    assert data == rivers and isinstance(updated_at, str) and updated_at
    db.put_river_snapshot("MD", [])                  # upsert overwrites
    data2, _ = db.get_river_snapshot("MD")
    assert data2 == []


def test_snapshot_stale(monkeypatch):
    import datetime as _dt
    monkeypatch.setattr(main, "_REFRESH_INTERVAL", 1000)
    now = _dt.datetime.now(_dt.timezone.utc)
    assert main._snapshot_stale(now.isoformat()) is False
    assert main._snapshot_stale(
        (now - _dt.timedelta(seconds=5000)).isoformat()) is True
    assert main._snapshot_stale("not-a-date") is True


def test_rivers_for_state_serves_snapshot_no_usgs(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._state_rivers_cache.clear()
    seeded = [{"name": "X", "site_no": "1", "color": "#2ecc71"}]
    db.put_river_snapshot("MD", seeded)

    async def boom(*a, **k):
        raise AssertionError("USGS must never be on the request path")

    monkeypatch.setattr(main, "_usgs_iv", boom)
    sched = []
    monkeypatch.setattr(main, "_schedule_state_refresh",
                        lambda st: sched.append(st))
    out = asyncio.run(main._rivers_for_state_cached("MD"))
    assert out == seeded and sched == []             # fresh -> no refresh


def test_rivers_for_state_stale_serves_and_refreshes(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._state_rivers_cache.clear()
    db.put_river_snapshot("MD", [{"name": "X", "site_no": "1"}])
    monkeypatch.setattr(main, "_REFRESH_INTERVAL", -1)   # everything stale
    sched = []
    monkeypatch.setattr(main, "_schedule_state_refresh",
                        lambda st: sched.append(st))
    out = asyncio.run(main._rivers_for_state_cached("MD"))
    assert out and sched == ["MD"]                   # served stale + refresh


def test_rivers_for_state_lazy_empty_and_schedules(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._state_rivers_cache.clear()
    sched = []
    monkeypatch.setattr(main, "_schedule_state_refresh",
                        lambda st: sched.append(st))
    out = asyncio.run(main._rivers_for_state_cached("WY"))
    assert out == [] and sched == ["WY"]             # lazy: fills next cycle


def test_precompute_refresh_state_persists(tmp_path, monkeypatch):
    import asyncio
    import precompute
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._state_rivers_cache.clear()

    ts = [{
        "sourceInfo": {
            "siteName": "Gunpowder Falls near Glencoe, MD",
            "siteCode": [{"value": "01581920"}],
            "geoLocation": {"geogLocation": {"latitude": 39.566,
                                             "longitude": -76.605}},
        },
        "variable": {"variableDescription": "Streamflow, ft3/s"},
        "values": [{"value": [{"value": "95",
                               "dateTime": "2026-05-19T08:00:00"}]}],
    }]

    async def fake_iv(extra, label):
        return {"value": {"timeSeries": ts}}

    async def no_medians(nos):
        return None

    async def no_backfill(rivers):
        return None

    monkeypatch.setattr(main, "_usgs_iv", fake_iv)
    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(main, "_trout_for_state", lambda st: None)
    monkeypatch.setattr(precompute, "_backfill_geometry", no_backfill)

    out = asyncio.run(precompute.refresh_state("MD"))
    assert len(out) == 1 and out[0]["name"] == "Gunpowder Falls"
    snap = db.get_river_snapshot("MD")
    assert snap is not None
    rivers, _ = snap
    assert rivers and rivers[0]["name"] == "Gunpowder Falls"
    assert main._state_rivers_cache.get("MD") == rivers   # L1 warmed


def test_river_lines_payload_from_db(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "LineString",
                      "coordinates": [[-77, 39], [-77.1, 39.1]]}}]}
    db.put_river_geom("01581920", fc)
    rivers = [{"site_no": "01581920", "color": "#2ecc71"},
              {"site_no": "99999999", "color": "#e74c3c"}]  # no geom -> miss
    out, missing = asyncio.run(main._river_lines_payload(rivers))
    assert out["type"] == "FeatureCollection"
    assert len(out["features"]) == 1
    f = out["features"][0]
    assert f["properties"] == {"site_no": "01581920", "color": "#2ecc71"}
    assert f["geometry"]["type"] == "LineString"
    assert len(missing) == 1 and missing[0]["site_no"] == "99999999"


def test_refresh_focused_single_flight(monkeypatch):
    """External cron + in-process loop both call refresh_focused; if one
    is already running the other must skip rather than double USGS load."""
    import asyncio
    import precompute
    monkeypatch.setattr(precompute, "_refresh_running", True)
    calls = []

    async def fake_refresh_state(st, *, backfill=True):
        calls.append(st)
        return []

    monkeypatch.setattr(precompute, "refresh_state", fake_refresh_state)
    asyncio.run(precompute.refresh_focused())
    assert calls == []                              # early-return, no work
    assert precompute._refresh_running is True      # flag untouched


def test_internal_refresh_requires_token(monkeypatch):
    import asyncio
    req = SimpleNamespace(headers={})
    with pytest.raises(HTTPException) as ei:                  # token unset
        asyncio.run(main.internal_refresh(req))
    assert ei.value.status_code == 403
    monkeypatch.setattr(main, "_REFRESH_TOKEN", "secret")
    bad = SimpleNamespace(headers={"x-refresh-token": "nope"})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.internal_refresh(bad))
    assert ei.value.status_code == 403


# -- river identity via NHD GNIS (the screenshot-bug fix) --

def test_db_gauge_meta_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    assert db.get_gauge_meta("01581920") is None
    db.put_gauge_meta("01581920",
                      {"comid": "12345", "gnis_name": "Gunpowder Falls"})
    assert db.get_gauge_meta("01581920") == {
        "comid": "12345", "gnis_name": "Gunpowder Falls"}
    db.put_gauge_meta("01589000", {"comid": "67890", "gnis_name": None})
    got = db.get_gauge_metas(["01581920", "01589000", "99999999"])
    assert got["01581920"]["gnis_name"] == "Gunpowder Falls"
    assert got["01589000"]["gnis_name"] is None
    assert "99999999" not in got                     # absent, not error


def test_nldi_gauge_meta_short_circuits_on_db(tmp_path, monkeypatch):
    """Once a gauge's NHD identity is in Postgres, no network call."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    main._gauge_meta_cache.clear()
    db.put_gauge_meta("01581920",
                      {"comid": "12345", "gnis_name": "Gunpowder Falls"})

    class NoNet:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k):
            raise AssertionError("DB hit should preempt the NLDI call")

    monkeypatch.setattr(main.httpx, "Client", NoNet)
    meta = main._nldi_gauge_meta("01581920")
    # gauge_meta written without levelpathid (pre-VAA) gets it
    # backfilled (None when VAA isn't loaded), all without a net call.
    assert meta["comid"] == "12345"
    assert meta["gnis_name"] == "Gunpowder Falls"
    assert meta.get("levelpathid") is None


def test_nldi_gauge_meta_graceful(monkeypatch):
    """NLDI down => returns {} (caller falls back to heuristic naming);
    empties are NOT persisted to DB so they retry later."""
    main._gauge_meta_cache.clear()
    monkeypatch.setattr(db, "get_gauge_meta", lambda s: None)
    persisted = {}
    monkeypatch.setattr(db, "put_gauge_meta",
                        lambda s, m: persisted.setdefault(s, m))

    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): raise RuntimeError("nldi down")

    monkeypatch.setattr(main.httpx, "Client", Boom)
    assert main._nldi_gauge_meta("00000000") == {}
    assert persisted == {}                            # empties never persisted


def test_assemble_rivers_prefers_gnis_name(monkeypatch):
    """When gauge_meta has a gnis_name, _assemble_rivers labels by NHD,
    not by the USGS station name -- so e.g. 'Georges Run near
    Beckleysville, MD' stops standing in for 'Gunpowder Falls'."""
    import asyncio

    async def no_medians(nos):
        return None

    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(
        db, "get_gauge_metas",
        lambda nos: {"01581920": {"comid": "1", "gnis_name": "Gunpowder Falls"}},
    )
    main._stats_cache.clear()
    ts = [{
        "sourceInfo": {
            "siteName": "Georges Run near Beckleysville, MD",   # misleading
            "siteCode": [{"value": "01581920"}],
            "geoLocation": {"geogLocation": {"latitude": 39.62,
                                             "longitude": -76.69}},
        },
        "variable": {"variableDescription": "Streamflow, ft3/s"},
        "values": [{"value": [{"value": "9",
                               "dateTime": "2026-05-19T08:00:00"}]}],
    }]
    rivers = asyncio.run(main._assemble_rivers(ts, [None], []))
    assert len(rivers) == 1 and rivers[0]["name"] == "Gunpowder Falls"


def test_assemble_rivers_collects_levelpathids_per_river(monkeypatch):
    """River dicts carry each gauge's NHD levelpath so the client can
    unify a clicked clickable-stream reach with its gauged river even
    when NHD and NLDI disagree on the GNIS name for that reach."""
    import asyncio

    async def no_medians(nos):
        return None

    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(
        db, "get_gauge_metas",
        lambda nos: {
            "01582500": {"comid": "1", "gnis_name": "Gunpowder Falls",
                         "levelpathid": 200010762},
            "01581700": {"comid": "2", "gnis_name": "Gunpowder Falls",
                         "levelpathid": 200010762},
        },
    )
    main._stats_cache.clear()
    ts = [
        {"sourceInfo": {
            "siteName": "Gunpowder Falls at Glencoe, MD",
            "siteCode": [{"value": "01582500"}],
            "geoLocation": {"geogLocation": {"latitude": 39.62,
                                             "longitude": -76.62}}},
         "variable": {"variableDescription": "Streamflow, ft3/s"},
         "values": [{"value": [{"value": "150",
                                "dateTime": "2026-05-19T08:00:00"}]}]},
        {"sourceInfo": {
            "siteName": "Gunpowder Falls near Parkton, MD",
            "siteCode": [{"value": "01581700"}],
            "geoLocation": {"geogLocation": {"latitude": 39.65,
                                             "longitude": -76.66}}},
         "variable": {"variableDescription": "Streamflow, ft3/s"},
         "values": [{"value": [{"value": "40",
                                "dateTime": "2026-05-19T08:00:00"}]}]},
    ]
    rivers = asyncio.run(main._assemble_rivers(ts, [None], []))
    assert len(rivers) == 1
    assert rivers[0]["levelpathids"] == [200010762]


def test_assemble_rivers_falls_back_to_heuristic_without_gnis(monkeypatch):
    """If gauge_meta lookup returns nothing, the station-name heuristic
    is still used -- behavior unchanged for not-yet-backfilled gauges."""
    import asyncio

    async def no_medians(nos):
        return None

    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(db, "get_gauge_metas", lambda nos: {})
    main._stats_cache.clear()
    ts = [{
        "sourceInfo": {
            "siteName": "Gunpowder Falls near Glencoe, MD",
            "siteCode": [{"value": "01581920"}],
            "geoLocation": {"geogLocation": {"latitude": 39.62,
                                             "longitude": -76.69}},
        },
        "variable": {"variableDescription": "Streamflow, ft3/s"},
        "values": [{"value": [{"value": "9",
                               "dateTime": "2026-05-19T08:00:00"}]}],
    }]
    rivers = asyncio.run(main._assemble_rivers(ts, [None], []))
    assert len(rivers) == 1 and rivers[0]["name"] == "Gunpowder Falls"


# -- NHDPlusV2 LevelPathID filter (the topological flowline filter) --

def _seed_vaa(tmp_path, monkeypatch, rows):
    """Wire a fresh sqlite to db.DB_PATH and seed nhdplus_vaa rows.
    Returns the DB path so callers can keep using it."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    for r in rows:
        with db._conn() as conn:                                  # noqa: SLF001
            conn.execute(
                "INSERT INTO nhdplus_vaa (comid, hydroseq, levelpathid,"
                " streamlevel, gnis_name, lengthkm) VALUES (?,?,?,?,?,?)",
                (r["comid"], r.get("hydroseq"), r["levelpathid"],
                 r.get("streamlevel"), r.get("gnis_name"),
                 r.get("lengthkm")))
            conn.commit()


def test_db_vaa_lookup_single_and_batched(tmp_path, monkeypatch):
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 100, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
        {"comid": 200, "levelpathid": 6000, "gnis_name": "Minebank Run"},
        {"comid": 300, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
    ])
    assert db.get_vaa(100)["levelpathid"] == 5000
    assert db.get_vaa(999) is None                                # absent
    got = db.get_vaas([100, 200, 999])
    assert set(got) == {100, 200}
    assert got[100]["gnis_name"] == "Gunpowder Falls"


def test_bulk_load_vaa_idempotent(tmp_path, monkeypatch):
    """Loader runs once, then short-circuits."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    # Tiny bundled CSV fixture
    import csv, gzip
    fixture = tmp_path / "vaa.csv.gz"
    with gzip.open(fixture, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["comid", "hydroseq", "levelpathid",
                                          "streamlevel", "gnis_name", "lengthkm"])
        w.writeheader()
        w.writerow({"comid": 100, "hydroseq": 1, "levelpathid": 5000,
                    "streamlevel": 3, "gnis_name": "Gunpowder Falls",
                    "lengthkm": 1.5})
        w.writerow({"comid": 200, "hydroseq": 2, "levelpathid": 6000,
                    "streamlevel": 4, "gnis_name": "Minebank Run",
                    "lengthkm": 0.3})

    n = db.bulk_load_vaa(str(fixture))
    assert n == 2
    n2 = db.bulk_load_vaa(str(fixture))               # already loaded
    assert n2 == 0
    assert db.get_vaa(100)["gnis_name"] == "Gunpowder Falls"


def test_filter_flowlines_by_levelpath_keeps_only_matching(tmp_path, monkeypatch):
    """The Georges-Run-onto-Gunpowder case, by LevelPathID this time:
    walked features span two LevelPathIDs; filter keeps only the
    gauge's path."""
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 111, "levelpathid": 5000, "gnis_name": "Georges Run"},
        {"comid": 222, "levelpathid": 9999, "gnis_name": "Gunpowder Falls"},
        {"comid": 333, "levelpathid": 9999, "gnis_name": "Gunpowder Falls"},
    ])
    feats = [
        {"type": "Feature", "properties": {"nhdplus_comid": "111"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-76.69, 39.61], [-76.69, 39.60]]}},
        {"type": "Feature", "properties": {"nhdplus_comid": "222"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-76.69, 39.55], [-76.70, 39.50]]}},
        {"type": "Feature", "properties": {"nhdplus_comid": "333"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-76.71, 39.45], [-76.72, 39.40]]}},
    ]
    kept = main._filter_flowlines_by_levelpath(feats, target_lpid=5000)
    assert len(kept) == 1
    assert kept[0]["properties"]["nhdplus_comid"] == "111"


def test_filter_flowlines_by_levelpath_no_fallback_on_mismatch(tmp_path, monkeypatch):
    """Critical: when no features share the target LevelPathID, return
    empty -- never fall back to the unfiltered set. (That fallback is
    exactly what re-introduced the cross-confluence bleed previously.)"""
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 222, "levelpathid": 9999, "gnis_name": "Gunpowder Falls"},
    ])
    feats = [
        {"type": "Feature", "properties": {"nhdplus_comid": "222"},
         "geometry": {"type": "LineString",
                      "coordinates": [[-76.69, 39.55], [-76.70, 39.50]]}},
    ]
    kept = main._filter_flowlines_by_levelpath(feats, target_lpid=5000)
    assert kept == []


def test_nldi_gauge_meta_includes_levelpath_when_vaa_loaded(tmp_path, monkeypatch):
    """gauge_meta carries `levelpathid` pulled from the local VAA when
    the gauge's COMID is present."""
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 12345, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
    ])
    main._gauge_meta_cache.clear()

    class FakeClient:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            if "/nwissite/" in url:
                return _FakeResp({"features": [
                    {"properties": {"comid": 12345}}]})
            return _FakeResp({"features": [
                {"properties": {"gnis_name": "Gunpowder Falls"}}]})

    monkeypatch.setattr(main.httpx, "Client", FakeClient)
    meta = main._nldi_gauge_meta("01581920")
    assert meta["comid"] == "12345"
    assert meta["gnis_name"] == "Gunpowder Falls"
    assert meta["levelpathid"] == 5000


def test_nldi_flowline_prefers_levelpath_filter(tmp_path, monkeypatch):
    """When VAA covers the gauge AND the walked features, the
    LevelPathID filter runs (not the gnis fallback)."""
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 12345, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
        {"comid": 111, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
        {"comid": 222, "levelpathid": 9999, "gnis_name": "Long Green Creek"},
    ])
    main._river_geom_cache.clear()
    monkeypatch.setattr(
        main, "_nldi_gauge_meta",
        lambda s: {"comid": "12345", "gnis_name": "Gunpowder Falls",
                   "levelpathid": 5000})
    gnis_called = {"n": 0}

    def fake_gnis_filter(feats, target):
        gnis_called["n"] += 1
        return feats

    monkeypatch.setattr(main, "_filter_flowlines_by_gnis", fake_gnis_filter)

    seg_own = {"type": "Feature", "properties": {"nhdplus_comid": "111"},
               "geometry": {"type": "LineString",
                            "coordinates": [[-76.69, 39.61], [-76.69, 39.60]]}}
    seg_other = {"type": "Feature", "properties": {"nhdplus_comid": "222"},
                 "geometry": {"type": "LineString",
                              "coordinates": [[-76.69, 39.55], [-76.7, 39.5]]}}

    monkeypatch.setattr(
        main, "_fetch_nav",
        lambda c, b, nav, dist: [seg_own] if nav == "UM" else [seg_other])

    class CtxNoop:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): raise AssertionError("fetcher monkeypatched")
    monkeypatch.setattr(main.httpx, "Client", lambda *a, **k: CtxNoop())

    fc = main._nldi_flowline("01581920")
    assert gnis_called["n"] == 0                         # gnis tier didn't run
    assert [f["properties"]["nhdplus_comid"] for f in fc["features"]] == ["111"]


def test_shell_no_cache_middleware():
    """App-shell paths get no-cache so a CDN can't strand clients on a
    stale build; immutable vendored assets are unaffected."""
    import asyncio
    from types import SimpleNamespace

    class _Resp:
        def __init__(self): self.headers = {}

    async def _call_next(_req):
        return _Resp()

    def cc(path):
        req = SimpleNamespace(url=SimpleNamespace(path=path))
        resp = asyncio.run(main._shell_no_cache(req, _call_next))
        return resp.headers.get("Cache-Control")

    for p in ("/", "/map", "/sw.js", "/static/app.js", "/static/app.css"):
        assert cc(p) == "no-cache, must-revalidate", p
    for p in ("/static/vendor/leaflet/leaflet.js", "/api/rivers"):
        assert cc(p) is None, p


# -- geopandas-trim: shapely-only trout proximity + data-download seam --

def test_trout_layer_proximity_shapely():
    """TroutLayer.near uses a shapely STRtree (no geopandas). A gauge
    within ~450m of a stream line tags on-trout; one well away doesn't."""
    import trout
    line = {"type": "Feature", "properties": {},
            "geometry": {"type": "LineString",
                         "coordinates": [[-76.70, 39.60], [-76.70, 39.66]]}}
    layer = trout.TroutLayer([line])
    assert trout.is_near_trout_stream(39.63, -76.701, layer) is True   # ~85m off
    assert trout.is_near_trout_stream(39.63, -76.90, layer) is False   # ~17km off
    assert trout.is_near_trout_stream(39.63, -76.70, None) is False    # no layer


def test_trout_slim_simplifies_and_strips(monkeypatch):
    """load_trout_streams turns fetched features into a geometry-only
    TroutLayer via shapely (attributes dropped, vertices decimated)."""
    import trout
    trout._trout_cache.clear()
    raw = [{"type": "Feature", "properties": {"NAME": "X", "junk": 1},
            "geometry": {"type": "LineString",
                         "coordinates": [[-76.7, 39.6], [-76.7, 39.61],
                                         [-76.7, 39.62]]}}]
    monkeypatch.setattr(trout, "fetch_geojson_features", lambda url: raw)
    monkeypatch.setitem(trout.TROUT_SOURCES, "ZZ", {"name": "t", "url": "http://x"})
    layer = trout.load_trout_streams("ZZ")
    assert isinstance(layer, trout.TroutLayer)
    assert layer.features[0]["properties"] == {}          # stripped
    assert layer.features[0]["geometry"]["type"] == "LineString"


def test_resolve_data_file_local_first(tmp_path, monkeypatch):
    import data_source
    local = tmp_path / "vaa.csv.gz"
    local.write_bytes(b"local")
    monkeypatch.setattr(data_source, "DATA_BASE_URL", "")
    assert data_source.resolve_data_file(str(local), "vaa.csv.gz") == str(local)
    # missing local + no base URL -> returns the (absent) local path unchanged
    missing = str(tmp_path / "nope.gz")
    assert data_source.resolve_data_file(missing, "nope.gz") == missing


def test_resolve_data_file_downloads_when_configured(tmp_path, monkeypatch):
    import data_source

    class _Stream:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def raise_for_status(self): pass
        def iter_bytes(self): yield b"remote-bytes"

    monkeypatch.setattr(data_source, "DATA_BASE_URL", "https://data.example")
    monkeypatch.setattr(data_source, "_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(data_source.httpx, "stream", lambda *a, **k: _Stream())
    missing = str(tmp_path / "absent.gz")
    out = data_source.resolve_data_file(missing, "absent.gz")
    assert out != missing and open(out, "rb").read() == b"remote-bytes"


# -- lower-48 scale: bbox lat index + cache sizing --

def test_clickable_streams_has_both_bbox_indexes(tmp_path, monkeypatch):
    """At national scale the lon-only index leaves half the table for
    the lat predicate to scan; init_db must create both."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    with db._conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name='clickable_streams'")
        names = {row[0] for row in cur.fetchall()}
    assert "idx_clk_lon" in names
    assert "idx_clk_lat" in names


def test_caches_sized_for_lower_48_fanout():
    """48-state lazy fanout: caches must hold the full national working
    set without thrashing. Regression-protect the maxsize bumps."""
    assert main._state_rivers_cache.maxsize >= 48
    assert main._stats_cache.maxsize >= 6000
    assert main._river_geom_cache.maxsize >= 1024
