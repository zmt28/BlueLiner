"""Phase 1 auth: magic-link round-trip, sessions, _owner upgrade,
anonymous-pin claim. Offline -- monkeypatches email_send so no real
email leaves the process."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
import main


def _fresh(tmp_path, monkeypatch):
    """Point db at a clean sqlite + reset the in-memory rate buckets."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "auth.db"))
    db.init_db()
    main._auth_hits.clear()


def _req(cookies=None, ip="9.9.9.9", headers=None):
    return SimpleNamespace(
        cookies=cookies or {},
        client=SimpleNamespace(host=ip),
        headers=headers or {},
    )


# -- DB layer ----------------------------------------------------------

def test_upsert_user_by_email_idempotent(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u1 = db.upsert_user_by_email("Alice@Example.COM")
    u2 = db.upsert_user_by_email("alice@example.com")
    assert u1["id"] == u2["id"]               # same row, case-folded
    assert u1["email"] == "alice@example.com"
    assert u1["display_name"] == "alice"      # local-part default


def test_magic_link_single_use(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    db.create_magic_link("alice@example.com", "tok-A")
    assert db.consume_magic_link("tok-A") == "alice@example.com"
    assert db.consume_magic_link("tok-A") is None       # replayed
    assert db.consume_magic_link("never-issued") is None


def test_magic_link_expiry(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    db.create_magic_link("a@example.com", "tok-B")
    # Force-expire the row
    import sqlite3
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    c = sqlite3.connect(db.DB_PATH)
    c.execute("UPDATE magic_links SET expires_at=? WHERE token_hash=?",
              (past, db._hash("tok-B")))
    c.commit()
    assert db.consume_magic_link("tok-B") is None


def test_session_lifecycle_and_user_lookup(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-1", "ua", "1.2.3.4")
    got = db.user_from_session("sess-1")
    assert got and got["id"] == user["id"]
    db.delete_session("sess-1")
    assert db.user_from_session("sess-1") is None


def test_soft_delete_user_revokes_sessions(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-X", None, None)
    db.soft_delete_user(user["id"])
    assert db.user_from_session("sess-X") is None
    assert db.get_user(user["id"]) is None    # not visible to live lookups


def test_claim_pins_relinks_anonymous_to_user(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    device = "device-hash-deadbeef"
    db.add_pin(39.0, -77.0, "gravel lot", device)
    db.add_pin(39.1, -77.1, "bridge", device)
    user = db.upsert_user_by_email("alice@example.com")
    user_owner = f"user:{user['id']}"
    n = db.claim_pins(device, user_owner)
    assert n == 2
    assert db.list_pins(device) == []         # device no longer sees them
    assert len(db.list_pins(user_owner)) == 2


# -- _owner upgrade ----------------------------------------------------

def test_owner_prefers_session_over_device_token(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-Y", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-Y"},
               headers={"x-device-token": "raw-device-token-12345678"})
    assert main._owner(req) == f"user:{user['id']}"


def test_owner_falls_back_to_device_token_when_no_session(monkeypatch):
    main._auth_hits.clear()
    req = _req(headers={"x-device-token": "raw-device-token-12345678"})
    owner = main._owner(req, required=False)
    assert owner and owner != f"user:1"
    assert len(owner) == 64                   # SHA-256 hex


def test_owner_required_raises_400_without_either():
    req = _req()
    with pytest.raises(HTTPException) as ei:
        main._owner(req, required=True)
    assert ei.value.status_code == 400


# -- endpoints (in-process) -------------------------------------------

def test_request_link_creates_row_and_sends(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    sent = {}

    def fake_send(email, url, ttl):
        sent["email"], sent["url"], sent["ttl"] = email, url, ttl
        return True

    import email_send
    monkeypatch.setattr(email_send, "send_magic_link", fake_send)
    req = _req(ip="203.0.113.5")
    # Mimic FastAPI: request.base_url -> URL-ish; tests use a string and
    # the route reads it via str()/rstrip
    req.base_url = "http://localhost:8000/"
    body = main._MagicLinkIn(email="alice@example.com")
    asyncio.run(main.api_request_magic_link(body, req))
    assert sent["email"] == "alice@example.com"
    assert sent["url"].startswith("http://localhost:8000/auth/consume?token=")
    assert sent["ttl"] == db.MAGIC_LINK_TTL_MINUTES


def test_request_link_rate_limit(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    monkeypatch.setattr(main, "_AUTH_RATE_MAX", 3)
    import email_send
    monkeypatch.setattr(email_send, "send_magic_link",
                        lambda *a, **k: True)
    body = main._MagicLinkIn(email="alice@example.com")
    req = _req(ip="203.0.113.99")
    req.base_url = "http://localhost/"
    for _ in range(3):
        asyncio.run(main.api_request_magic_link(body, req))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.api_request_magic_link(body, req))
    assert ei.value.status_code == 429


def test_consume_creates_user_and_sets_cookie(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    db.create_magic_link("alice@example.com", "tok-C")
    resp = asyncio.run(main.auth_consume("tok-C"))
    assert resp.status_code == 200
    sc = resp.headers.get("set-cookie") or ""
    assert main._SESSION_COOKIE in sc and "HttpOnly" in sc
    # Used token can't be replayed
    resp2 = asyncio.run(main.auth_consume("tok-C"))
    assert resp2.status_code == 400


def test_logout_clears_session_and_cookie(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-OUT", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-OUT"})
    resp = asyncio.run(main.api_logout(req))
    assert db.user_from_session("sess-OUT") is None
    sc = resp.headers.get("set-cookie") or ""
    assert main._SESSION_COOKIE in sc and "Max-Age=0" in sc


def test_me_requires_session(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(main.api_me(_req()))
    assert ei.value.status_code == 401


def test_me_update_display_name(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-ME", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-ME"})
    out = asyncio.run(main.api_me_update(
        main._DisplayNameIn(display_name="Alice in Wonderland"), req))
    assert out["display_name"] == "Alice in Wonderland"


def test_me_delete_soft_deletes(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-DEL", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-DEL"})
    asyncio.run(main.api_me_delete(req))
    assert db.get_user(user["id"]) is None


def test_claim_pins_endpoint(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    device_token = "raw-device-token-deadbeef"
    import hashlib
    device_hash = hashlib.sha256(device_token.encode()).hexdigest()
    db.add_pin(39.0, -77.0, "gravel lot", device_hash)
    db.add_pin(39.1, -77.1, "bridge", device_hash)
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-CL", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-CL"},
               headers={"x-device-token": device_token})
    out = asyncio.run(main.api_pins_claim(req))
    assert out == {"claimed": 2}
    # Subsequent claim finds nothing new
    out2 = asyncio.run(main.api_pins_claim(req))
    assert out2 == {"claimed": 0}


def test_claimable_lists_only_device_pins(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    device_token = "raw-device-token-feedface"
    import hashlib
    device_hash = hashlib.sha256(device_token.encode()).hexdigest()
    db.add_pin(39.0, -77.0, "by the bridge", device_hash)
    other_user = db.upsert_user_by_email("bob@example.com")
    db.add_pin(40.0, -78.0, "bob's spot", f"user:{other_user['id']}")
    user = db.upsert_user_by_email("alice@example.com")
    db.create_session(user["id"], "sess-LST", None, None)
    req = _req(cookies={main._SESSION_COOKIE: "sess-LST"},
               headers={"x-device-token": device_token})
    out = asyncio.run(main.api_pins_claimable(req))
    assert len(out) == 1 and out[0]["note"] == "by the bridge"
