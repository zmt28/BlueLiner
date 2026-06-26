"""Pure unit tests (no network, no app lifespan) for the core logic."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import hatches
import stocking
import access_points
import db
import main


# -- hatches --

def test_zone_for_gunpowder_is_limestone_tailwater():
    z = hatches.zone_for(39.6361, -76.6889)
    assert z["name"] == "Limestone & Tailwater"


def test_zone_for_regions_and_fallback():
    # mid-Atlantic still picks up the fine-grained zones first
    assert hatches.zone_for(38.51, -80.54)["name"] == "Mountain Freestone"
    assert hatches.zone_for(38.07, -77.5)["name"] == "Blue Ridge / Piedmont"
    # post-national-rollout: regional zones cover the lower 48
    assert hatches.zone_for(45.0, -120.0)["name"] == "Pacific Northwest"  # OR
    assert hatches.zone_for(46.5, -110.0)["name"] == "Northern Rockies"  # MT
    assert hatches.zone_for(39.5, -106.0)["name"] == "Southern Rockies / Intermountain"  # CO
    assert hatches.zone_for(37.5, -119.0)["name"] == "Sierra Nevada / California"  # CA Sierra
    assert hatches.zone_for(35.8, -83.5)["name"] == "Southern Appalachians"  # Smokies
    assert hatches.zone_for(44.0, -91.5)["name"] == "Driftless / Upper Midwest"  # WI
    assert hatches.zone_for(45.0, -84.0)["name"] == "Great Lakes"  # MI
    assert hatches.zone_for(36.5, -92.5)["name"] == "Ozarks"  # AR
    assert hatches.zone_for(43.5, -71.0)["name"] == "Northeast / New England"  # NH
    # truly outside any zone (e.g. southern Florida) -> generic fallback
    assert hatches.zone_for(25.5, -80.3)["name"] == "Continental US (general)"


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


# -- access points (served from the national river-POI overlay) --

@pytest.fixture
def stub_access_overlay(monkeypatch):
    """Stub the pre-built national overlay so the access tests don't need the
    R2 file. monkeypatch restores the originals; we clear the per-state cache
    on both sides so nothing leaks between tests."""
    by_state = {
        "MD": [
            {"name": "MD Ramp", "type": "boat_ramp", "access": "public",
             "source": "agency", "precision": "surveyed",
             "agency_url": "https://md.gov", "lat": 39.576, "lon": -76.613},
            {"name": "MD Walk", "type": "walk_in", "access": "public",
             "source": "osm", "precision": "mapped",
             "lat": 39.600, "lon": -76.600},
        ],
    }
    monkeypatch.setattr(access_points, "_overlay_loaded", True)
    monkeypatch.setattr(access_points, "_overlay_by_state", by_state)
    access_points._access_cache.clear()
    yield by_state
    access_points._access_cache.clear()


def test_access_points_geojson_shape(stub_access_overlay):
    """The /api/access response shape: GeoJSON FeatureCollection of
    Points with the canonical attrs travelling in properties so the
    client can render type-coded icons and popups without a second
    request."""
    fc = access_points.access_points_geojson("MD")
    assert fc["type"] == "FeatureCollection"
    assert fc["features"], "MD should have at least one access point"
    f0 = fc["features"][0]
    assert f0["geometry"]["type"] == "Point"
    lon, lat = f0["geometry"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    props = f0["properties"]
    for k in ("name", "type", "access", "source", "precision"):
        assert k in props
    # The {lat, lon} keys are folded into geometry; not duplicated in
    # properties (would confuse downstream consumers).
    assert "lat" not in props and "lon" not in props


def test_access_unsupported_state_is_empty(stub_access_overlay):
    """A state the overlay has no points for -- e.g. Kansas today -- yields an
    empty FeatureCollection. The client-side checkbox still works; the layer
    simply has no markers."""
    fc = access_points.access_points_geojson("KS")
    assert fc == {"type": "FeatureCollection", "features": []}


def test_stocking_geojson_shape():
    """The /api/stocking response shape: GeoJSON Points with the stocked
    water's fields (season pre-formatted) in properties for direct pin +
    popup rendering."""
    import stocking
    fc = stocking.stocking_geojson("MD")
    assert fc["type"] == "FeatureCollection"
    assert fc["features"], "MD should have baseline stocked waters"
    f0 = fc["features"][0]
    assert f0["geometry"]["type"] == "Point"
    lon, lat = f0["geometry"]["coordinates"]
    assert -180 <= lon <= 180 and -90 <= lat <= 90
    props = f0["properties"]
    assert set(props) >= {"water", "species", "category", "season",
                          "agency_url", "source"}
    assert isinstance(props["species"], list)
    assert "lat" not in props and "lon" not in props
    # Unsupported state -> empty FC.
    assert stocking.stocking_geojson("XX") == {"type": "FeatureCollection",
                                               "features": []}


def test_stocking_features_to_points_skips_bad(caplog):
    """A malformed live feature is skipped (and logged), not allowed to
    drop the whole overlay -- replacing the old silent pass."""
    import stocking
    feats = [
        {"geometry": {"type": "Point", "coordinates": [-77.0, 39.0]},
         "properties": {"WATER": "Good Creek", "SPECIES": "Brown, Rainbow"}},
        {"geometry": None, "properties": {"WATER": "No geom"}},   # skipped
        {"properties": {"WATER": "Missing geom key"}},            # skipped
    ]
    pts = stocking._features_to_points(feats, {"agency_url": "http://agency"})
    assert len(pts) == 1
    p = pts[0]
    assert p["water"] == "Good Creek"
    assert p["species"] == ["Brown", "Rainbow"]
    assert p["lat"] == 39.0 and p["lon"] == -77.0
    assert p["agency_url"] == "http://agency"


def test_stocking_season_from_props():
    import stocking
    assert stocking._season_from_props({"SEASON_MONTHS": "3-6"}) == (3, 6)
    assert stocking._season_from_props({}) == (1, 12)           # absent
    assert stocking._season_from_props({"Season": "spring"}) == (1, 12)  # no nums
    assert stocking._season_from_props({"SEASON_MONTHS": "99-6"}) == (1, 12)  # bad


def test_nearby_access_proximity_and_ordering(stub_access_overlay):
    """Spatial query mirrors stocking.nearby_stocked: nearest-first,
    bounded by ~buffer_deg. Used for any future 'nearby access' popup
    section parallel to 'stocked nearby'."""
    pts = access_points.load_access_points("MD")
    # Query at the MD Ramp coordinate; both stubbed MD points fall inside
    # ~0.05 deg, the ramp first (distance 0).
    hits = access_points.nearby_access(39.576, -76.613, pts, buffer_deg=0.05)
    assert hits, "should return at least one nearby access"
    # Nearest-first: each successive hit is at least as far as the prior.
    def d2(p):
        return (p["lat"] - 39.576) ** 2 + (p["lon"] + 76.613) ** 2
    distances = [d2(p) for p in hits]
    assert distances == sorted(distances)
    assert hits[0]["name"] == "MD Ramp"


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
    # status_code / headers added for _nldi_get's retry path; existing
    # tests that don't pass them still get a "success" response.
    status_code = 200
    headers: dict = {}
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


def test_states_in_bbox():
    import states
    md_area = states.states_in_bbox(-79.0, 38.0, -76.0, 40.0)
    assert "MD" in md_area and "VA" in md_area
    assert "CA" not in md_area and "FL" not in md_area
    co_area = states.states_in_bbox(-106.0, 38.5, -104.5, 40.0)
    assert "CO" in co_area
    assert states.states_in_bbox(-30.0, 10.0, -29.0, 11.0) == []  # Atlantic ocean


def test_point_in_state():
    import states
    assert states.point_in_state(39.4, -76.4) == "MD"       # Gunpowder Falls
    assert states.point_in_state(39.0, -105.5) == "CO"      # Colorado Rockies
    assert states.point_in_state(10.0, -30.0) is None       # Atlantic ocean


def test_reach_detail_payload_shape():
    # Gunpowder Falls, MD -- a reach with hatch zone + likely nearby data.
    payload = main._reach_detail_payload(39.4, -76.4, "Gunpowder Falls")
    assert set(payload) == {"hatch", "access", "stocked", "trout"}
    # River-level trout block always present; class may be None (no
    # evidence) but the keys are stable for the card renderer.
    assert set(payload["trout"]) == {"river_class", "river_label"}
    assert isinstance(payload["hatch"]["active"], list)
    assert isinstance(payload["access"], list)
    assert isinstance(payload["stocked"], list)
    # Hatch entries are trimmed to the card's fields.
    for e in payload["hatch"]["active"]:
        assert set(e) >= {"common_name", "patterns"}
        assert len(e["patterns"]) <= 2
    # Ocean point -> no state -> empty access/stocked, hatch still resolves.
    empty = main._reach_detail_payload(10.0, -30.0, None)
    assert empty["access"] == [] and empty["stocked"] == []


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
    # Pill labels are sentence case per the design system spec
    # ("Trout water" / "Stocked water nearby", not Title Case). The
    # stocked/access pills say "nearby" -- they're click/centroid
    # proximity signals, not river-wide claims (W3 honest copy).
    assert "Stocked water nearby" in html                   # near_stocked pill
    assert "Trout water" in html                            # on_trout pill
    assert "Stocked nearby" in html                         # stocked block
    assert "Gunpowder falls near glencoe, md" in html       # gauge sub-header
    assert 'data-site="01581920"' in html                   # chart placeholder
    assert "Flow context" in html                           # median present
    # TroutRoutes-style panel redesign: peek-friendly header with stat
    # grid + pill row + condition badge, followed by a CSS-radio tab bar
    # whose four panels carry the existing Conditions/Hatches/Stocking/
    # Log-catch sections. <details> remains for the inner gauge sub-
    # accordions inside the Conditions tab.
    assert "bl-pills" in html                               # pill row exists
    assert "pill--trout" in html and "pill--stocked" in html
    assert "bl-stats" in html and "bl-stat-n" in html       # stat grid
    assert 'class="bl-tabs"' in html                        # tabbed body
    assert 'id="bl-tab-conditions"' in html                 # default-checked tab
    assert 'data-tab="conditions"' in html
    assert 'data-tab="hatches"' in html
    assert 'data-tab="stocking"' in html
    assert 'data-tab="catch"' in html
    assert 'class="bl-flow-chart"' in html
    # Hatches tab content still renders the seasonal block.
    assert "Hatching now" in html or "Hatching soon" in html
    # Tab-bar redesign: the catch CTA is now its own tab panel after
    # gauges (Conditions tab default), not a banner above the gauges.
    # Verify it still exists and sits inside its own labeled panel.
    assert 'data-tab="catch"' in html
    assert "bl-catch-cta" in html
    # Directions row: both map apps offered, each routed to the river's coords.
    assert 'class="bl-dir-row"' in html
    assert "maps.apple.com/?daddr=39.6,-76.7" in html        # Apple Maps
    assert "google.com/maps/dir/?api=1&destination=39.6,-76.7" in html  # Google


def test_directions_row_html_requires_coords():
    row = main._directions_row_html({"lat": 39.6, "lon": -76.7})
    assert "Apple Maps" in row and "Google Maps" in row
    assert "maps.apple.com" in row and "google.com/maps/dir" in row
    # Missing/None coords -> no row (no broken link to nowhere).
    assert main._directions_row_html({"lat": None, "lon": None}) == ""
    assert main._directions_row_html({}) == ""


def test_directions_row_html_labels_apple_with_name():
    """Apple Maps link carries the name as `q=` so it shows the river instead
    of reverse-geocoding the coordinate to a nearby address; `daddr` still
    routes to the exact coordinate, and Google stays unlabeled."""
    row = main._directions_row_html(
        {"name": "Gunpowder Falls", "lat": 39.6, "lon": -76.7})
    assert "daddr=39.6,-76.7" in row          # routes to the exact coordinate
    assert "q=Gunpowder%20Falls" in row       # Apple destination label
    # No name -> no label (no regression from prior behavior).
    assert "q=" not in main._directions_row_html({"lat": 39.6, "lon": -76.7})


def test_build_reach_popup_html_unified():
    # Ungauged reach -> the SAME full panel as a gauged river, but with
    # no gauges: Conditions tab carries a "no gauge" note, Hatches is the
    # default-checked tab, and the trout pill reflects the `trout` flag.
    html = main.build_reach_popup_html(39.6, -76.7, "Gunpowder Falls", True)
    assert 'class="bl-tabs"' in html                        # same tabbed body
    assert 'data-tab="conditions"' in html and 'data-tab="hatches"' in html
    assert "No USGS gauge on this reach" in html            # conditions note
    assert "bl-flow-chart" not in html                      # no gauge -> no chart
    assert "Trout water" in html                             # trout=True pill
    # Default tab is Hatches (a hatch is active here in season-agnostic
    # data) or, off-season, Conditions -- either way one radio is checked.
    assert "checked" in html
    # Untrout reach -> the standardized "No trout designation" pill.
    html2 = main.build_reach_popup_html(39.6, -76.7, "Some Creek", False)
    assert "No trout designation" in html2
    assert "pill--none" in html2


def test_ranking_summary_leads_with_label():
    # The condition score is folded into the verdict callout: a "good"
    # river leads with a "Good" label, not a separate title-row badge.
    river = {
        "overall": "green",
        "gauges": [{
            "site_no": "X",
            "variables": [{"variable": "Streamflow", "value": "85"}],
            "conditions": {"current_flow": 85.0, "temp_f": 55.0},
            "historical_median": 80.0}],
    }
    s = main._ranking_summary_html(river)
    assert 'class="verdict-label">Good<' in s
    assert "near normal" in s


def test_panel_header_drops_badge_keeps_trout_pill():
    # No more .cond title-row badge; trout pill is always present (the
    # designation, or "No trout designation").
    base = {
        "name": "Test R", "overall": "yellow", "near_stocked": False,
        "active": [], "access_count": 0, "gauges": [],
        "stocked_waters": [], "hatch_zone": {"name": "Z"}, "month": 5,
    }
    designated = main._panel_header_html({**base, "on_trout": True})
    assert "cond--" not in designated                       # badge removed
    assert "Trout water" in designated
    undesignated = main._panel_header_html({**base, "on_trout": False})
    assert "No trout designation" in undesignated


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

    # Numbers in the verdict get wrapped in <strong> (design-system
    # spec); the substring matches need to allow for that markup.
    # 60 vs 80 median -> 25% below; 13C -> 55.4F ideal
    s = main._ranking_summary_html(river(60.0, 80.0, 13))
    assert "<strong>25%</strong> below average" in s and "ideal" in s
    assert "for this time of year" in s        # time-bound comparison
    # 160 vs 80 -> 100% above; 21C -> 69.8F too warm
    s = main._ranking_summary_html(river(160.0, 80.0, 21))
    assert "<strong>100%</strong> above average" in s and "too warm" in s
    # within 15% -> near normal
    s = main._ranking_summary_html(river(85.0, 80.0, 10))
    assert "near normal" in s
    # no median -> raw cfs; no temp
    s = main._ranking_summary_html(river(42.0, None, None))
    assert "<strong>42</strong> cfs" in s
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

def test_db_river_stats_roundtrip(tmp_path, monkeypatch):
    import datetime as _dt
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()

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
    # Per-gauge points carry the USGS site's own coordinates so the client
    # can render one condition icon per gauge at its real location.
    gauges = rivers[0]["gauges"]
    assert len(gauges) == 1
    assert gauges[0]["lat"] == 39.566 and gauges[0]["lon"] == -76.605
    assert gauges[0]["site_no"] == "01581920"
    assert gauges[0]["conditions"]["overall"] in ("green", "yellow", "red")


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

    async def no_backfill(site_nos):
        return None

    monkeypatch.setattr(main, "_usgs_iv", fake_iv)
    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(main, "_trout_for_state", lambda st: None)
    monkeypatch.setattr(precompute, "_backfill_gauge_meta", no_backfill)

    out = asyncio.run(precompute.refresh_state("MD"))
    assert len(out) == 1 and out[0]["name"] == "Gunpowder Falls"
    snap = db.get_river_snapshot("MD")
    assert snap is not None
    rivers, _ = snap
    assert rivers and rivers[0]["name"] == "Gunpowder Falls"
    assert main._state_rivers_cache.get("MD") == rivers   # L1 warmed


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


def test_db_vaa_lookup_single(tmp_path, monkeypatch):
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 100, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
        {"comid": 200, "levelpathid": 6000, "gnis_name": "Minebank Run"},
        {"comid": 300, "levelpathid": 5000, "gnis_name": "Gunpowder Falls"},
    ])
    assert db.get_vaa(100)["levelpathid"] == 5000
    assert db.get_vaa(999) is None                                # absent
    assert db.get_vaa(200)["gnis_name"] == "Minebank Run"


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


def test_bulk_load_vaa_upgrades_to_elevation(tmp_path, monkeypatch):
    """A warm table loaded from the old 6-column CSV is auto-reloaded when
    an elevation-bearing CSV arrives (the national rollout) -- no manual
    TRUNCATE. A same-schema reload still short-circuits."""
    import csv
    import gzip
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()

    old = tmp_path / "vaa_old.csv.gz"
    with gzip.open(old, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["comid", "hydroseq", "levelpathid",
                                          "streamlevel", "gnis_name",
                                          "lengthkm"])
        w.writeheader()
        w.writerow({"comid": 100, "hydroseq": 1, "levelpathid": 5000,
                    "streamlevel": 3, "gnis_name": "Gunpowder Falls",
                    "lengthkm": 1.5})
    assert db.bulk_load_vaa(str(old)) == 1
    assert db.vaa_has_elevation() is False
    # Re-running the same elevation-less CSV must NOT reload.
    assert db.bulk_load_vaa(str(old)) == 0

    new = tmp_path / "vaa_new.csv.gz"
    with gzip.open(new, "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["comid", "hydroseq", "levelpathid",
                                          "streamlevel", "gnis_name",
                                          "lengthkm", "maxelevsmo",
                                          "minelevsmo"])
        w.writeheader()
        w.writerow({"comid": 100, "hydroseq": 1, "levelpathid": 5000,
                    "streamlevel": 3, "gnis_name": "Gunpowder Falls",
                    "lengthkm": 1.5, "maxelevsmo": 21000, "minelevsmo": 18000})
        w.writerow({"comid": 200, "hydroseq": 2, "levelpathid": 5000,
                    "streamlevel": 3, "gnis_name": "Gunpowder Falls",
                    "lengthkm": 0.8, "maxelevsmo": 18000, "minelevsmo": 15000})
    # Elevation-bearing CSV: table is wiped and reloaded.
    assert db.bulk_load_vaa(str(new)) == 2
    assert db.vaa_has_elevation() is True
    assert db.get_vaa(200)["maxelevsmo"] == 18000
    # Now warm WITH elevation -> short-circuits again.
    assert db.bulk_load_vaa(str(new)) == 0


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


def test_nldi_gauge_meta_reresolves_null_levelpath_from_vaa(tmp_path, monkeypatch):
    """A row persisted with levelpathid=None (written while the national VAA
    was empty) is re-resolved from the VAA off its stored comid -- no NLDI
    call. This is what re-populates a river's levelpathids so a clicked
    reach matches its gauge by levelpath, not name alone."""
    _seed_vaa(tmp_path, monkeypatch, [
        {"comid": 12345, "levelpathid": 5000, "gnis_name": "North Platte River"},
    ])
    main._gauge_meta_cache.clear()
    # Simulate the empty-VAA-window row: comid known, levelpathid null.
    db.put_gauge_meta("06620000",
                      {"comid": "12345", "gnis_name": "North Platte River",
                       "levelpathid": None})

    # Any NLDI call would blow up -- proving the re-resolve is VAA-only.
    class Boom:
        def __init__(self, *a, **k): raise AssertionError("must not hit NLDI")

    monkeypatch.setattr(main.httpx, "Client", Boom)
    meta = main._nldi_gauge_meta("06620000")
    assert meta["levelpathid"] == 5000          # filled from the VAA
    assert meta["comid"] == "12345"
    # Persisted, so the next read short-circuits.
    assert db.get_gauge_meta("06620000")["levelpathid"] == 5000


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

    # Shell routes get the no-cache header so deploys propagate
    # immediately. (Vite hashes the JS/CSS bundle filenames, so
    # /static/dist/assets/* are themselves immutable -- /map is the
    # cache-busting choke point that points at fresh hashes.)
    for p in ("/", "/map", "/sw.js", "/static/manifest.webmanifest"):
        assert cc(p) == "no-cache, must-revalidate", p
    # Content-hashed Vite bundles + the icons keep their default
    # long-lived caching; the API has its own per-route cache rules.
    for p in ("/static/dist/assets/index-abc123.js",
              "/static/icons/icon-180.png", "/api/rivers"):
        assert cc(p) is None, p


def test_collapse_slashes_middleware():
    """`//api/x` is rewritten to `/api/x` before routing, so a stray double
    slash resolves to the real endpoint instead of FastAPI's default
    `{"detail":"Not Found"}`."""
    import asyncio

    seen = {}

    async def _inner(scope, receive, send):
        seen["path"] = scope["path"]
        seen["raw_path"] = scope.get("raw_path")

    mw = main._CollapseSlashesMiddleware(_inner)
    asyncio.run(mw({"type": "http", "path": "//api//elevation_profile",
                    "raw_path": b"//api//elevation_profile"}, None, None))
    assert seen["path"] == "/api/elevation_profile"
    assert seen["raw_path"] == b"/api/elevation_profile"
    # A clean path passes through untouched.
    seen.clear()
    asyncio.run(mw({"type": "http", "path": "/api/rivers",
                    "raw_path": b"/api/rivers"}, None, None))
    assert seen["path"] == "/api/rivers"


def test_stocking_geometry_cleaning():
    """XYZM (4D) geometries are trimmed to 2D and null-coordinate features
    dropped, so a feed that publishes either doesn't blank the whole
    overlay (the `kept 0, skipped N` live-feed regression)."""
    assert stocking._clean_coords([-78.5, 38.1, 600.0, 12.3]) == [-78.5, 38.1]
    assert stocking._clean_coords([-78.6, None]) is None
    assert stocking._geom_2d({"type": "Point", "coordinates": [1, 2, 3, 4]}) == \
        {"type": "Point", "coordinates": [1.0, 2.0]}
    feats = [
        {"geometry": {"type": "Point", "coordinates": [-78.5, 38.1, 600.0, 12.3]},
         "properties": {"WATER": "Trout Run"}},          # 4D -> trimmed, kept
        {"geometry": {"type": "Point", "coordinates": [-78.6, None]},
         "properties": {"WATER": "Bad Coord"}},          # null X/Y -> dropped
        {"geometry": {"type": "Point", "coordinates": [-78.7, 38.2]},
         "properties": {"WATER": "Clean Creek"}},        # normal -> kept
        {"geometry": None, "properties": {"WATER": "No Geom"}},  # dropped
    ]
    pts = stocking._features_to_points(
        feats, {"state": "VA", "name_field": "WATER"})
    assert {p["water"] for p in pts} == {"Trout Run", "Clean Creek"}
    tp = next(p for p in pts if p["water"] == "Trout Run")
    assert round(tp["lon"], 1) == -78.5 and round(tp["lat"], 1) == 38.1


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


def test_shell_injects_data_version_meta(tmp_path, monkeypatch):
    import data_source
    shell = tmp_path / "index.html"
    shell.write_text("<!doctype html><head><title>BL</title></head><body></body>")
    monkeypatch.setattr(data_source, "DATA_BASE_URL", "https://data.example/v7")
    main._shell_cache.clear()
    out = main._shell_with_data_version(str(shell)).decode()
    assert '<meta name="bl-data-version" content="v7">' in out
    # injected right after <head> so it parses before the client scripts run
    assert out.index("bl-data-version") < out.index("<title>")
    main._shell_cache.clear()


def test_data_version_from_base_url(monkeypatch):
    import data_source
    # The R2 version prefix is the cache-buster the client appends as ?v=.
    monkeypatch.setattr(data_source, "DATA_BASE_URL", "https://data.example/v4")
    assert data_source.data_version() == "v4"
    monkeypatch.setattr(data_source, "DATA_BASE_URL", "https://data.example/v12")
    assert data_source.data_version() == "v12"
    # Unset -> bundled files -> a stable sentinel (never an empty ?v=).
    monkeypatch.setattr(data_source, "DATA_BASE_URL", "")
    assert data_source.data_version() == "local"


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


# -- free-tier memory: cache sizing --

def test_caches_sized_for_free_tier_memory():
    """Caches must fit the 512MB free tier, not just the national working
    set. A _stats_cache entry is a full year of daily medians (~39KB each),
    so its maxsize sets the single largest in-process consumer: 6000 was
    ~230MB (the OOM driver), 2000 is ~77MB. Postgres (db.river_stats) is the
    durable L2, so a smaller cap costs extra local DB reads under wide fanout,
    never USGS calls. Guard the bound both ways -- big enough to stay useful,
    small enough to leave headroom under the 512MB ceiling."""
    assert 1000 <= main._stats_cache.maxsize <= 3000
    # _state_rivers_cache is TTL'd (120s), so it can't accumulate all 51
    # states in steady state; the cap is just a burst guard.
    assert main._state_rivers_cache.maxsize <= 64


# -- NLDI backoff on 429/503 throttling --

def test_nldi_get_retries_on_429_then_succeeds(monkeypatch):
    """USGS NLDI throttles aggressively under the focused-states geom
    backfill. Without retry, a single 429 lost the gauge's flowline
    for the full ~45 min refresh interval. _nldi_get should retry on
    429 and return the first non-throttled response."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)  # no real sleep
    seq = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(429),
        httpx.Response(200, json={"features": []}),
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        i = calls["n"]
        calls["n"] += 1
        return seq[i]

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        resp = main._nldi_get(c, "https://nldi.test/x")
    assert resp is not None
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_nldi_get_gives_up_after_max_retries(monkeypatch):
    """If the throttle window outlasts our retry budget, return the
    final 429 so the caller logs+returns empty (geometry retried next
    refresh cycle) rather than hanging the worker."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_429(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429)

    with httpx.Client(transport=httpx.MockTransport(always_429)) as c:
        resp = main._nldi_get(c, "https://nldi.test/x")
    assert resp is not None
    assert resp.status_code == 429
    assert calls["n"] == main._NLDI_MAX_RETRIES + 1  # initial + retries


def test_nldi_get_returns_none_on_network_error(monkeypatch):
    """RequestError (timeout / DNS / reset) isn't retried -- bail
    immediately with None so the caller's except path runs cleanly."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)

    def broken(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset")

    with httpx.Client(transport=httpx.MockTransport(broken)) as c:
        resp = main._nldi_get(c, "https://nldi.test/x")
    assert resp is None


# -- root-redirect state resolution --

def _req(query: dict | None = None, headers: dict | None = None):
    """Minimal Request stub for _root_state: it only reads
    query_params.get and headers.get, both dict-shaped."""
    return SimpleNamespace(query_params=query or {}, headers=headers or {})


def test_root_state_default_is_maryland():
    """No explicit param, no geolocation header -> historical MD default."""
    assert main._root_state(_req()) == "MD"


def test_root_state_explicit_query_param_wins():
    """Explicit ?state= overrides geolocation -- a user pasting a link to
    Colorado shouldn't be redirected to their own state."""
    req = _req(query={"state": "co"},
               headers={"CF-IPCountry": "US", "CF-Region-Code": "MT"})
    assert main._root_state(req) == "CO"


def test_root_state_invalid_query_param_falls_through():
    """Garbage in the URL doesn't lock the user out of the geo default."""
    req = _req(query={"state": "ZZ"},
               headers={"CF-IPCountry": "US", "CF-Region-Code": "OR"})
    assert main._root_state(req) == "OR"


def test_root_state_uses_cloudflare_region_when_us():
    """When Cloudflare proxying is enabled on blueliner.app, the edge
    injects CF-Region-Code. US-only so a Canadian visitor doesn't end up
    on `BC` (not in STATES)."""
    req = _req(headers={"CF-IPCountry": "US", "CF-Region-Code": "CO"})
    assert main._root_state(req) == "CO"


def test_root_state_ignores_non_us_geolocation():
    """A user in Vancouver (BC) shouldn't get redirected to a state code
    that doesn't exist -- fall through to the MD default instead of
    breaking the map."""
    req = _req(headers={"CF-IPCountry": "CA", "CF-Region-Code": "BC"})
    assert main._root_state(req) == "MD"


def test_root_state_unknown_region_code_falls_back_to_default():
    """If CF gives us a region we don't recognise (e.g. an outlying US
    territory not in STATES), fall back rather than 302'ing into a
    broken state."""
    req = _req(headers={"CF-IPCountry": "US", "CF-Region-Code": "XX"})
    assert main._root_state(req) == "MD"


def test_stocked_block_html_empty_states():
    """An empty Stocking tab renders a clear message, not a blank panel --
    and distinguishes 'none near this river' from 'no data for the state'."""
    near = main._stocked_block_html([], has_state_data=True)
    assert "No stocked waters" in near and "bl-reach-msg" in near
    none = main._stocked_block_html([], has_state_data=False)
    assert "No stocking data for this state" in none
    block = main._stocked_block_html(
        [{"water": "Test Run", "species": ["Brown"], "category": "Stocked",
          "season_months": (3, 6), "agency_url": "http://x"}])
    assert "Test Run" in block and "No stocked waters" not in block


def test_assemble_rivers_stocking_probes_each_gauge(monkeypatch):
    """Stocking is matched near ANY of a river's gauges, not just the
    centroid -- so a stocked water by an end gauge of a long river isn't
    lost when the centroid sits >2 km away (the Gunpowder false-'0' case)."""
    import asyncio

    async def no_medians(nos):
        return None

    monkeypatch.setattr(main, "_ensure_medians_cached", no_medians)
    monkeypatch.setattr(db, "get_gauge_metas", lambda nos: {
        "01": {"comid": "1", "gnis_name": "Test River"},
        "02": {"comid": "2", "gnis_name": "Test River"},
    })
    main._stats_cache.clear()

    def site(code, lat):
        return {
            "sourceInfo": {"siteName": f"Test River gauge {code}",
                           "siteCode": [{"value": code}],
                           "geoLocation": {"geogLocation": {
                               "latitude": lat, "longitude": -76.0}}},
            "variable": {"variableDescription": "Streamflow, ft3/s"},
            "values": [{"value": [{"value": "9",
                                   "dateTime": "2026-05-19T08:00:00"}]}],
        }
    ts = [site("01", 40.0), site("02", 40.10)]   # centroid ~40.05
    # Stocked water right by the northern gauge, ~6 km from the centroid:
    # a centroid-only probe (0.02 deg) misses it; per-gauge catches it.
    stocked = [{"water": "North Stock", "lat": 40.105, "lon": -76.0,
                "species": ["Rainbow"], "category": "Stocked",
                "season_months": (1, 12), "agency_url": "http://x"}]
    rivers = asyncio.run(main._assemble_rivers(ts, [None], stocked))
    assert len(rivers) == 1
    assert rivers[0]["near_stocked"] is True
    assert "North Stock" in rivers[0]["popup_html"]
