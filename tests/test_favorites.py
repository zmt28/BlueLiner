"""M4.1: favorite waters CRUD + condition-alert diffing. Offline --
email sends are monkeypatched so nothing leaves the process."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
import email_send
import favorites
import main


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "fav.db"))
    db.init_db()


def _signed_in(tmp_path, monkeypatch, email="angler@example.com"):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email(email)
    db.create_session(user["id"], "sess-fav", None, None)
    return user, SimpleNamespace(
        cookies={main._SESSION_COOKIE: "sess-fav"},
        client=SimpleNamespace(host="9.9.9.9"), headers={})


# -- DB layer ----------------------------------------------------------

def test_favorite_crud_and_owner_isolation(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    alice = db.upsert_user_by_email("alice@example.com")
    bob = db.upsert_user_by_email("bob@example.com")

    f = db.add_favorite(alice["id"], "01581920", "Gunpowder Falls", "MD",
                        39.5, -76.6)
    assert f["site_no"] == "01581920" and f["notify"] is True

    favs = db.list_favorites(alice["id"])
    assert [x["site_no"] for x in favs] == ["01581920"]
    assert db.list_favorites(bob["id"]) == []            # isolation

    # Upsert refreshes name/coords but keeps notify + last_overall.
    db.set_favorite_notify(alice["id"], "01581920", False)
    db.set_favorite_verdict(alice["id"], "01581920", "yellow")
    db.add_favorite(alice["id"], "01581920", "Gunpowder", "MD", 39.51, -76.61)
    only = db.list_favorites(alice["id"])[0]
    assert only["name"] == "Gunpowder"
    assert only["notify"] is False
    assert only["last_overall"] == "yellow"

    assert db.remove_favorite(bob["id"], "01581920") is False  # not bob's
    assert db.remove_favorite(alice["id"], "01581920") is True
    assert db.list_favorites(alice["id"]) == []


def test_favorites_for_state_joins_email(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("angler@example.com")
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.add_favorite(u["id"], "01589000", "Patapsco River", "MD", 39.3, -76.8)
    db.add_favorite(u["id"], "02055000", "Roanoke River", "VA", 37.3, -79.9)

    md = db.favorites_for_state("MD")
    assert len(md) == 2
    assert all(f["email"] == "angler@example.com" for f in md)
    assert {f["site_no"] for f in md} == {"01581920", "01589000"}


# -- Alert diffing (favorites.check_favorite_alerts) --------------------

def _rivers(overall):
    return [{"site_no": "01581920", "name": "Gunpowder Falls",
             "conditions": {"overall": overall}}]


def _capture_alerts(monkeypatch):
    """Capture digest sends as (email, items) tuples."""
    sent: list[tuple] = []
    monkeypatch.setattr(
        email_send, "send_condition_digest",
        lambda email, items: sent.append((email, items)) or True)
    return sent


def test_first_observation_is_silent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    sent = _capture_alerts(monkeypatch)

    assert favorites.check_favorite_alerts("MD", _rivers("green")) == 0
    assert sent == []
    assert db.list_favorites(u["id"])[0]["last_overall"] == "green"


def test_transitions_alert_only_into_green_or_red(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.set_favorite_verdict(u["id"], "01581920", "yellow")
    sent = _capture_alerts(monkeypatch)

    # yellow -> green: alert
    assert favorites.check_favorite_alerts("MD", _rivers("green")) == 1
    assert sent[-1] == ("a@example.com",
                        [{"name": "Gunpowder Falls", "state": "MD",
                          "prev": "yellow", "new": "green"}])
    # green -> yellow: state updates, no alert
    assert favorites.check_favorite_alerts("MD", _rivers("yellow")) == 0
    assert db.list_favorites(u["id"])[0]["last_overall"] == "yellow"
    # yellow -> red: alert ("don't bother driving")
    assert favorites.check_favorite_alerts("MD", _rivers("red")) == 1
    # unchanged verdict: nothing
    assert favorites.check_favorite_alerts("MD", _rivers("red")) == 0
    assert len(sent) == 2


def test_notify_off_updates_state_without_email(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.set_favorite_verdict(u["id"], "01581920", "red")
    db.set_favorite_notify(u["id"], "01581920", False)
    sent = _capture_alerts(monkeypatch)

    assert favorites.check_favorite_alerts("MD", _rivers("green")) == 0
    assert sent == []
    # State still tracked, so re-enabling alerts doesn't replay this one.
    assert db.list_favorites(u["id"])[0]["last_overall"] == "green"


def test_missing_gauge_keeps_prior_state(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("a@example.com")
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.set_favorite_verdict(u["id"], "01581920", "green")
    sent = _capture_alerts(monkeypatch)

    assert favorites.check_favorite_alerts(
        "MD", [{"site_no": "99999999", "conditions": {"overall": "red"}}]) == 0
    assert sent == []
    assert db.list_favorites(u["id"])[0]["last_overall"] == "green"


# -- Digest batching + budgets (M5.4) -----------------------------------

def _two_rivers(a_overall, b_overall):
    return [
        {"site_no": "01581920", "name": "Gunpowder Falls",
         "conditions": {"overall": a_overall}},
        {"site_no": "01589000", "name": "Patapsco River",
         "conditions": {"overall": b_overall}},
    ]


def _two_favorites(email="a@example.com"):
    u = db.upsert_user_by_email(email)
    db.add_favorite(u["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.add_favorite(u["id"], "01589000", "Patapsco River", "MD", 39.3, -76.8)
    db.set_favorite_verdict(u["id"], "01581920", "yellow")
    db.set_favorite_verdict(u["id"], "01589000", "yellow")
    return u


def test_transitions_digest_into_one_email_per_user(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _two_favorites()
    sent = _capture_alerts(monkeypatch)

    # Both favorites transition in the same pass -> ONE email, 2 items.
    assert favorites.check_favorite_alerts("MD", _two_rivers("green", "red")) == 1
    assert len(sent) == 1
    email, items = sent[0]
    assert email == "a@example.com"
    assert {(it["name"], it["new"]) for it in items} == {
        ("Gunpowder Falls", "green"), ("Patapsco River", "red")}


def test_digests_are_per_user(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _two_favorites("a@example.com")
    b = db.upsert_user_by_email("b@example.com")
    db.add_favorite(b["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.set_favorite_verdict(b["id"], "01581920", "yellow")
    sent = _capture_alerts(monkeypatch)

    assert favorites.check_favorite_alerts("MD", _two_rivers("green", "green")) == 2
    assert {e for e, _ in sent} == {"a@example.com", "b@example.com"}


def test_user_daily_cap_skips_but_still_records_verdict(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = _two_favorites()
    sent = _capture_alerts(monkeypatch)
    monkeypatch.setattr(favorites, "USER_DAILY_ALERT_CAP", 1)

    assert favorites.check_favorite_alerts("MD", _two_rivers("green", "yellow")) == 1
    # Second digest of the day: capped, no email -- but the verdict is
    # already recorded, so the transition never replays.
    assert favorites.check_favorite_alerts("MD", _two_rivers("red", "yellow")) == 0
    assert len(sent) == 1
    gunpowder = next(f for f in db.list_favorites(u["id"])
                     if f["site_no"] == "01581920")
    assert gunpowder["last_overall"] == "red"
    # Unchanged pass stays silent even when budget would allow.
    monkeypatch.setattr(favorites, "USER_DAILY_ALERT_CAP", 10)
    assert favorites.check_favorite_alerts("MD", _two_rivers("red", "yellow")) == 0


def test_global_budget_stops_all_sends(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    _two_favorites("a@example.com")
    b = db.upsert_user_by_email("b@example.com")
    db.add_favorite(b["id"], "01581920", "Gunpowder Falls", "MD", 39.5, -76.6)
    db.set_favorite_verdict(b["id"], "01581920", "yellow")
    sent = _capture_alerts(monkeypatch)
    monkeypatch.setattr(favorites, "GLOBAL_DAILY_EMAIL_BUDGET", 1)

    # Two users transition; the global budget allows one send total.
    assert favorites.check_favorite_alerts("MD", _two_rivers("green", "green")) == 1
    assert len(sent) == 1


def test_try_spend_email_budget_limits_per_day(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert db.try_spend_email_budget("alerts:global", 2) is True
    assert db.try_spend_email_budget("alerts:global", 2) is True
    assert db.try_spend_email_budget("alerts:global", 2) is False
    # Scopes are independent.
    assert db.try_spend_email_budget("alerts:user:1", 2) is True
    # A zero/negative limit never allows a send.
    assert db.try_spend_email_budget("alerts:user:2", 0) is False


# -- API routes ---------------------------------------------------------

def test_api_favorites_crud(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)

    fav = asyncio.run(main.api_add_favorite(
        main._FavoriteIn(site_no="01581920", name="Gunpowder Falls",
                         state="md", lat=39.5, lon=-76.6), req))
    assert fav["state"] == "MD"                    # normalized

    out = asyncio.run(main.api_list_favorites(req))
    assert [f["site_no"] for f in out["favorites"]] == ["01581920"]

    patched = asyncio.run(main.api_patch_favorite(
        "01581920", main._FavoritePatch(notify=False), req))
    assert patched["notify"] is False

    asyncio.run(main.api_remove_favorite("01581920", req))
    assert asyncio.run(main.api_list_favorites(req))["favorites"] == []

    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_remove_favorite("01581920", req))
    assert e.value.status_code == 404


def test_api_favorites_requires_auth_and_valid_state(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    anon = SimpleNamespace(cookies={}, client=SimpleNamespace(host="9.9.9.9"),
                           headers={})
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_list_favorites(anon))
    assert e.value.status_code == 401

    _, req = _signed_in(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_add_favorite(
            main._FavoriteIn(site_no="x", name="y", state="ZZ"), req))
    assert e.value.status_code == 400
