"""M5.3: passkey storage + challenge bookkeeping. The full WebAuthn
crypto round-trip needs a real/virtual authenticator, so it's covered
by the Playwright E2E (CDP virtual authenticator), not here."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import db
import main


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "pk.db"))
    db.init_db()
    main._auth_hits.clear()
    main._wa_reg_challenges.clear()
    main._wa_auth_challenges.clear()


def _signed_in(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    user = db.upsert_user_by_email("angler@example.com")
    db.create_session(user["id"], "sess-pk", None, None)
    return user, SimpleNamespace(
        cookies={main._SESSION_COOKIE: "sess-pk"},
        client=SimpleNamespace(host="9.9.9.9"), headers={},
        url=SimpleNamespace(hostname="localhost", scheme="http",
                            netloc="localhost:8000"))


# -- DB layer ----------------------------------------------------------

def test_credential_crud_and_owner_guard(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    alice = db.upsert_user_by_email("alice@example.com")
    bob = db.upsert_user_by_email("bob@example.com")
    db.add_webauthn_credential(alice["id"], "cred-A", "pubkey-A", 0,
                               "internal", "Chrome on Mac")

    got = db.get_webauthn_credential("cred-A")
    assert got["email"] == "alice@example.com"
    assert got["sign_count"] == 0
    assert db.get_webauthn_credential("cred-nope") is None

    keys = db.list_webauthn_credentials(alice["id"])
    assert len(keys) == 1 and keys[0]["nickname"] == "Chrome on Mac"
    assert keys[0]["last_used_at"] is None

    db.touch_webauthn_credential("cred-A", 7)
    assert db.get_webauthn_credential("cred-A")["sign_count"] == 7
    assert db.list_webauthn_credentials(alice["id"])[0]["last_used_at"]

    # Bob can't remove Alice's passkey.
    assert db.delete_webauthn_credential(bob["id"], "cred-A") is False
    assert db.delete_webauthn_credential(alice["id"], "cred-A") is True
    assert db.get_webauthn_credential("cred-A") is None


def test_deleted_user_credentials_stop_authenticating(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    u = db.upsert_user_by_email("gone@example.com")
    db.add_webauthn_credential(u["id"], "cred-G", "pk", 0, None, None)
    db.soft_delete_user(u["id"])
    assert db.get_webauthn_credential("cred-G") is None


# -- Challenge store ----------------------------------------------------

def test_challenge_single_use_and_ttl(monkeypatch):
    store: dict = {}
    main._wa_put_challenge(store, "h1", b"challenge-1")
    assert main._wa_take_challenge(store, "h1") == b"challenge-1"
    assert main._wa_take_challenge(store, "h1") is None      # single-use
    # Expired entries don't redeem.
    import time as _time
    store["h2"] = (b"challenge-2", _time.time() - 999)
    assert main._wa_take_challenge(store, "h2") is None


def test_challenge_store_bounded():
    store: dict = {}
    for i in range(1100):
        main._wa_put_challenge(store, f"h{i}", b"c")
    assert len(store) <= 1000


# -- Endpoints (sans crypto) --------------------------------------------

def test_register_options_requires_auth(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    anon = SimpleNamespace(cookies={}, client=SimpleNamespace(host="1.1.1.1"),
                           headers={},
                           url=SimpleNamespace(hostname="localhost",
                                               scheme="http",
                                               netloc="localhost:8000"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_webauthn_register_options(anon))
    assert e.value.status_code == 401


def test_register_options_stores_challenge(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    resp = asyncio.run(main.api_webauthn_register_options(req))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert '"rp"' in body and '"challenge"' in body
    assert user["id"] in main._wa_reg_challenges


def test_auth_options_returns_handle(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    req = SimpleNamespace(cookies={}, client=SimpleNamespace(host="2.2.2.2"),
                          headers={},
                          url=SimpleNamespace(hostname="localhost",
                                              scheme="http",
                                              netloc="localhost:8000"))
    out = asyncio.run(main.api_webauthn_auth_options(req))
    assert out["handle"] in main._wa_auth_challenges
    assert "challenge" in out["options"]


def test_authenticate_rejects_unknown_credential(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    req = SimpleNamespace(cookies={}, client=SimpleNamespace(host="3.3.3.3"),
                          headers={},
                          url=SimpleNamespace(hostname="localhost",
                                              scheme="http",
                                              netloc="localhost:8000"))
    out = asyncio.run(main.api_webauthn_auth_options(req))
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_webauthn_authenticate(
            main._WebAuthnAuthIn(handle=out["handle"],
                                 credential={"id": "cred-nope"}), req))
    assert e.value.status_code == 400
    # And the challenge burned: replaying the handle fails too.
    with pytest.raises(HTTPException):
        asyncio.run(main.api_webauthn_authenticate(
            main._WebAuthnAuthIn(handle=out["handle"],
                                 credential={"id": "cred-nope"}), req))


def test_nickname_from_ua():
    f = main._wa_nickname
    assert f("Mozilla/5.0 (Macintosh...) Chrome/120 Safari/537") == "Chrome on Mac"
    assert f("Mozilla/5.0 (iPhone...) Safari/604") == "Safari on iPhone/iPad"
    assert f(None) == "Passkey"


def test_passkey_management_endpoints(tmp_path, monkeypatch):
    user, req = _signed_in(tmp_path, monkeypatch)
    db.add_webauthn_credential(user["id"], "cred-M", "pk", 0, None,
                               "Firefox on Windows")
    out = asyncio.run(main.api_me_passkeys(req))
    assert [k["nickname"] for k in out["passkeys"]] == ["Firefox on Windows"]
    asyncio.run(main.api_me_delete_passkey("cred-M", req))
    assert asyncio.run(main.api_me_passkeys(req))["passkeys"] == []
    with pytest.raises(HTTPException) as e:
        asyncio.run(main.api_me_delete_passkey("cred-M", req))
    assert e.value.status_code == 404
