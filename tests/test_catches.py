"""Phase 2: catch-log CRUD + auto-enrichment. Offline -- USGS/NOAA are
monkeypatched so nothing leaves the process."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
import enrichment
import main
import weather


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "catch.db"))
    db.init_db()


def _signed_in(tmp_path, monkeypatch, email="angler@example.com"):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email(email)
    db.create_session(user["id"], "sess-catch", None, None)
    return user, SimpleNamespace(
        cookies={main._SESSION_COOKIE: "sess-catch"},
        client=SimpleNamespace(host="9.9.9.9"), headers={})


# -- DB layer ----------------------------------------------------------

def test_catch_crud_and_owner_isolation(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    alice = db.upsert_user_by_email("alice@example.com")
    bob = db.upsert_user_by_email("bob@example.com")
    c = db.add_catch(alice["id"], {
        "species": "Brown Trout", "river_name": "Gunpowder Falls",
        "length_in": 14.0, "fly_used": "Olive Woolly Bugger",
        "occurred_at": "2026-05-21T16:30:00+00:00",
    }, {"flow_cfs": 18.0, "moon_phase": "Waxing gibbous"})
    assert c["id"] and c["species"] == "Brown Trout"
    assert c["env"]["flow_cfs"] == 18.0          # env round-trips as dict
    assert c["visibility"] == "private"

    assert db.get_catch(c["id"])["river_name"] == "Gunpowder Falls"
    assert len(db.list_catches(alice["id"])) == 1
    assert db.list_catches(bob["id"]) == []      # isolation
    assert db.count_catches(alice["id"]) == 1

    # bob can't update or delete alice's catch
    assert db.update_catch(c["id"], bob["id"], {"species": "x"}) is None
    assert db.delete_catch(c["id"], bob["id"]) is False
    # alice can
    upd = db.update_catch(c["id"], alice["id"], {"length_in": 15.5})
    assert upd["length_in"] == 15.5
    assert db.delete_catch(c["id"], alice["id"]) is True
    assert db.get_catch(c["id"]) is None


def test_env_snapshot_immutable_on_update(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    c = db.add_catch(u["id"], {"species": "Rainbow"},
                     {"flow_cfs": 50.0})
    db.update_catch(c["id"], u["id"], {"species": "Brook Trout",
                                       "fly_used": "Adams"})
    after = db.get_catch(c["id"])
    assert after["species"] == "Brook Trout"
    assert after["env"] == {"flow_cfs": 50.0}    # untouched


def test_list_filters(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    db.add_catch(u["id"], {"species": "Brown Trout",
                           "occurred_at": "2026-05-01T12:00:00+00:00"}, None)
    db.add_catch(u["id"], {"species": "Rainbow Trout",
                           "occurred_at": "2026-05-20T12:00:00+00:00"}, None)
    assert len(db.list_catches(u["id"], species="Brown Trout")) == 1
    may = db.list_catches(u["id"], date_from="2026-05-10T00:00:00+00:00")
    assert len(may) == 1 and may[0]["species"] == "Rainbow Trout"
    # newest first
    allc = db.list_catches(u["id"])
    assert allc[0]["species"] == "Rainbow Trout"


# -- enrichment --------------------------------------------------------

def test_moon_phase_named():
    # Known new moon anchor -> "New moon"
    assert enrichment.moon_phase(
        datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)) == "New moon"
    # ~2 weeks later should be near full
    assert "Full" in enrichment.moon_phase(
        datetime(2000, 1, 21, tzinfo=timezone.utc)) or \
        "gibbous" in enrichment.moon_phase(
            datetime(2000, 1, 21, tzinfo=timezone.utc))


def test_build_env_degrades_gracefully(monkeypatch):
    # USGS + NOAA both "down": env still returns, fields are None
    monkeypatch.setattr(enrichment, "_usgs_now",
                        lambda s: {"flow_cfs": None, "water_temp_f": None})
    monkeypatch.setattr(weather, "fetch_observation", lambda lat, lon: {})
    env = enrichment.build_env(39.63, -76.68, "01581920",
                               "Gunpowder Falls",
                               datetime(2026, 5, 21, tzinfo=timezone.utc))
    assert set(env) >= {"flow_cfs", "air_temp_f", "moon_phase",
                        "active_hatches", "conditions"}
    assert env["flow_cfs"] is None and env["air_temp_f"] is None
    assert env["moon_phase"]                        # computed, never None
    assert isinstance(env["active_hatches"], list)  # hatches are local data


def test_build_env_full(monkeypatch):
    monkeypatch.setattr(enrichment, "_usgs_now",
                        lambda s: {"flow_cfs": 18.0, "water_temp_f": 52.0})
    monkeypatch.setattr(weather, "fetch_observation",
                        lambda lat, lon: {"air_temp_f": 61.0,
                                          "pressure_inhg": 30.05,
                                          "conditions": "Overcast"})
    monkeypatch.setattr(db, "get_river_stats",
                        lambda sites: {"01581920": {"5-21": 30.0}})
    env = enrichment.build_env(39.63, -76.68, "01581920",
                               "Gunpowder Falls",
                               datetime(2026, 5, 21, tzinfo=timezone.utc))
    assert env["flow_cfs"] == 18.0 and env["water_temp_f"] == 52.0
    assert env["flow_median_cfs"] == 30.0
    assert env["flow_vs_median"] == "below average"  # 18/30 = 0.6
    assert env["air_temp_f"] == 61.0 and env["conditions"] == "Overcast"


# -- endpoints ---------------------------------------------------------

def test_add_catch_requires_session(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    req = SimpleNamespace(cookies={}, client=SimpleNamespace(host="1.1.1.1"),
                          headers={})
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.api_add_catch(
            main._CatchIn(species="Brown Trout"), req))
    assert ei.value.status_code == 401


def test_add_catch_builds_env_and_persists(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    monkeypatch.setattr(enrichment, "build_env",
                        lambda *a, **k: {"flow_cfs": 18.0,
                                         "moon_phase": "Full moon"})
    body = main._CatchIn(species="Brown Trout", river_name="Gunpowder Falls",
                         river_site_no="01581920", lat=39.63, lon=-76.68,
                         length_in=14.0)
    catch = asyncio.run(main.api_add_catch(body, req))
    assert catch["species"] == "Brown Trout"
    assert catch["env"]["moon_phase"] == "Full moon"
    listing = asyncio.run(main.api_list_catches(
        req, species=None, date_from=None, date_to=None, limit=200))
    assert listing["total"] == 1


def test_add_catch_rejects_blank_species(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.api_add_catch(main._CatchIn(species="   "), req))
    assert ei.value.status_code == 422


def test_get_catch_owner_only(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    other = db.upsert_user_by_email("other@example.com")
    foreign = db.add_catch(other["id"], {"species": "Rainbow"}, None)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.api_get_catch(foreign["id"], req))
    assert ei.value.status_code == 404


def test_patch_and_delete_catch(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    monkeypatch.setattr(enrichment, "build_env", lambda *a, **k: {})
    catch = asyncio.run(main.api_add_catch(
        main._CatchIn(species="Brown Trout", lat=39.6, lon=-76.6), req))
    upd = asyncio.run(main.api_update_catch(
        catch["id"], main._CatchPatch(length_in=16.0), req))
    assert upd["length_in"] == 16.0
    asyncio.run(main.api_delete_catch(catch["id"], req))
    assert asyncio.run(main.api_list_catches(
        req, species=None, date_from=None, date_to=None, limit=200))["total"] == 0


class _FakeHttp:
    """Minimal httpx.Client stand-in: maps URL substrings -> JSON."""
    def __init__(self, routes): self._routes = routes
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get(self, url, params=None):
        for frag, payload in self._routes.items():
            if frag in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected URL {url}")


class _Resp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p


def test_usgs_now_parses_iv_json(monkeypatch):
    """Lock the prod USGS parse path (sandbox can't reach USGS live)."""
    iv = {"value": {"timeSeries": [
        {"variable": {"variableCode": [{"value": "00060"}]},
         "values": [{"value": [{"value": "18.4"}]}]},
        {"variable": {"variableCode": [{"value": "00010"}]},
         "values": [{"value": [{"value": "11.1"}]}]},  # 11.1C -> 52.0F
    ]}}
    monkeypatch.setattr(enrichment.httpx, "Client",
                        lambda *a, **k: _FakeHttp({"waterservices": iv}))
    out = enrichment._usgs_now("01581920")
    assert out["flow_cfs"] == 18.4
    assert out["water_temp_f"] == 52.0


def test_usgs_now_skips_nodata_sentinel(monkeypatch):
    iv = {"value": {"timeSeries": [
        {"variable": {"variableCode": [{"value": "00060"}]},
         "values": [{"value": [{"value": "-999999"}]}]},
    ]}}
    monkeypatch.setattr(enrichment.httpx, "Client",
                        lambda *a, **k: _FakeHttp({"waterservices": iv}))
    assert enrichment._usgs_now("x")["flow_cfs"] is None


def test_noaa_observation_parses(monkeypatch):
    """Lock the prod NOAA parse path (sandbox can't reach NOAA live)."""
    weather._station_cache.clear()
    weather._obs_cache.clear()
    routes = {
        "/points/": {"properties": {
            "observationStations": "https://api.weather.gov/x/stations"}},
        "/x/stations": {"features": [
            {"properties": {"stationIdentifier": "KDMW"}}]},
        "/stations/KDMW/observations/latest": {"properties": {
            "temperature": {"value": 16.1},          # -> 61.0F
            "barometricPressure": {"value": 101800},  # Pa -> ~30.06 inHg
            "textDescription": "Overcast",
            "timestamp": "2026-05-21T16:00:00+00:00"}},
    }
    monkeypatch.setattr(weather.httpx, "Client",
                        lambda *a, **k: _FakeHttp(routes))
    obs = weather.fetch_observation(39.63, -76.68)
    assert obs["air_temp_f"] == 61.0
    assert obs["conditions"] == "Overcast"
    assert 29.9 < obs["pressure_inhg"] < 30.2
    assert obs["station"] == "KDMW"


def test_enrichment_preview_endpoint(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    monkeypatch.setattr(enrichment, "build_env",
                        lambda *a, **k: {"flow_cfs": 22.0,
                                         "conditions": "Clear"})
    out = asyncio.run(main.api_enrichment_preview(
        req, lat=39.6, lon=-76.6, site_no="01581920",
        river_name="Gunpowder Falls", occurred_at=None))
    assert out["flow_cfs"] == 22.0 and out["conditions"] == "Clear"
