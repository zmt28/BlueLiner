"""
Outbound email via Resend (https://resend.com).

Tiny on purpose: one transactional template (magic-link sign-in),
HTTP-only, no SDK dependency. Set `RESEND_API_KEY` + `EMAIL_FROM` in
the environment for production. In dev (no API key) the call logs the
message and returns -- this lets the app run locally without an
email account and keeps tests fully offline.
"""

import logging
import os

import httpx

logger = logging.getLogger("blueliner.email")

_RESEND_API = "https://api.resend.com/emails"
_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
# Display name + sending address. Keep the address on a domain whose
# SPF/DKIM/DMARC are configured to authorize Resend.
_FROM = os.environ.get("EMAIL_FROM", "Blueliner <no-reply@blueliner.app>")


def _build_magic_link_html(consume_url: str, expires_minutes: int,
                           code: str | None = None) -> str:
    code_block = ""
    if code:
        code_block = (
            f'<p style="font-size:14px;color:#444;margin:20px 0 4px">'
            f'On a different device than the one you asked from? Type '
            f'this code there instead:</p>'
            f'<p style="font-size:28px;font-weight:700;'
            f'letter-spacing:6px;margin:4px 0 0">{code}</p>')
    return (
        f'<div style="font-family:system-ui,sans-serif;max-width:480px;'
        f'margin:0 auto;padding:24px;color:#222">'
        f'<h2 style="margin:0 0 16px">Sign in to Blueliner</h2>'
        f'<p>Tap the button below to finish signing in. The link works '
        f'on this device for the next {expires_minutes} minutes and can '
        f'only be used once.</p>'
        f'<p style="margin:24px 0">'
        f'<a href="{consume_url}" style="display:inline-block;'
        f'background:#1e6fd9;color:#fff;text-decoration:none;'
        f'padding:12px 20px;border-radius:6px;font-weight:600">'
        f'Sign in to Blueliner</a></p>'
        f'{code_block}'
        f'<p style="font-size:13px;color:#666">If the button doesn\'t '
        f'work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all">{consume_url}</span></p>'
        f'<p style="font-size:13px;color:#888;margin-top:24px">'
        f'Did not request this? You can safely ignore the email.</p>'
        f'</div>')


def _build_magic_link_text(consume_url: str, expires_minutes: int,
                           code: str | None = None) -> str:
    code_block = (f"\n\nOn a different device? Type this code there "
                  f"instead: {code}" if code else "")
    return (
        f"Sign in to Blueliner\n\n"
        f"Open this link to finish signing in (valid {expires_minutes} "
        f"minutes, single use):\n{consume_url}{code_block}\n\n"
        f"Did not request this? You can safely ignore the email.\n")


def send_magic_link(email: str, consume_url: str,
                    expires_minutes: int = 15,
                    code: str | None = None) -> bool:
    """Send the sign-in link (plus the cross-device fallback code when
    given) to `email`. Returns True on success. In dev (no API key)
    logs the URL and returns True so the flow can be tested locally by
    copy-pasting from the log."""
    if not _API_KEY:
        logger.info("(dev) magic-link for %s -> %s (code %s)",
                    email, consume_url, code or "-")
        return True
    payload = {
        "from": _FROM,
        "to": [email],
        "subject": "Sign in to Blueliner",
        "html": _build_magic_link_html(consume_url, expires_minutes, code),
        # Plain-text part keeps spam scoring down (M5.2e); a sign-in
        # email in the junk folder is a lost user.
        "text": _build_magic_link_text(consume_url, expires_minutes, code),
    }
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(_RESEND_API, json=payload,
                       headers={"Authorization": f"Bearer {_API_KEY}"})
            r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Resend send failed for %s: %s", email, exc)
        return False


# -- Condition alerts (M4.1, digested per pass in M5.4) ----------------

_VERDICT_LABEL = {"green": "Good", "yellow": "Fair", "red": "Poor",
                  "gray": "No data"}
_VERDICT_COLOR = {"green": "#2F6B3D", "yellow": "#8a6d1f", "red": "#8A3327",
                  "gray": "#5b6673"}


def _digest_subject(items: list[dict]) -> str:
    if len(items) == 1:
        it = items[0]
        return f"{it['name']} just went {_VERDICT_LABEL.get(it['new'], it['new'])}"
    good = sum(1 for it in items if it["new"] == "green")
    if good == len(items):
        return f"{len(items)} of your waters just went Good"
    if good == 0:
        return f"{len(items)} of your waters just went Poor"
    return f"{len(items)} of your waters just changed"


def _build_digest_html(items: list[dict]) -> str:
    rows = []
    for it in items:
        new_label = _VERDICT_LABEL.get(it["new"], it["new"])
        prev_label = _VERDICT_LABEL.get(it["prev"], it["prev"])
        color = _VERDICT_COLOR.get(it["new"], "#222")
        rows.append(
            f'<p style="margin:0 0 12px;font-size:15px">'
            f'<strong>{it["name"]}</strong> ({it["state"]}) just went '
            f'<strong style="color:{color}">{new_label}</strong> '
            f'(was {prev_label}).</p>')
    return (
        f'<div style="font-family:system-ui,sans-serif;max-width:480px;'
        f'margin:0 auto;padding:24px;color:#222">'
        f'<h2 style="margin:0 0 16px">Your waters changed</h2>'
        f'{"".join(rows)}'
        f'<p style="margin:24px 0">'
        f'<a href="https://blueliner.app/map" style="display:inline-block;'
        f'background:#15506C;color:#fff;text-decoration:none;'
        f'padding:12px 20px;border-radius:6px;font-weight:600">'
        f'Check the water</a></p>'
        f'<p style="font-size:12px;color:#888">You get these because '
        f'these are favorites with alerts on. Turn them off from '
        f'My Content &rarr; Favorites in the app.</p>'
        f'</div>'
    )


def send_condition_digest(email: str, items: list[dict]) -> bool:
    """One email covering every favorite of `email`'s that transitioned
    this precompute pass. `items`: [{"name", "state", "prev", "new"}].
    A single-item digest reads identically to the old per-river alert.
    In dev (no API key) logs and returns True, same as the magic link."""
    if not items:
        return False
    if not _API_KEY:
        logger.info("(dev) condition digest for %s: %s", email,
                    "; ".join(f"{it['name']} ({it['state']})"
                              f" {it['prev']} -> {it['new']}"
                              for it in items))
        return True
    payload = {
        "from": _FROM,
        "to": [email],
        "subject": _digest_subject(items),
        "html": _build_digest_html(items),
        "text": "Your waters changed\n\n" + "\n".join(
            f"- {it['name']} ({it['state']}): now "
            f"{_VERDICT_LABEL.get(it['new'], it['new'])} (was "
            f"{_VERDICT_LABEL.get(it['prev'], it['prev'])})"
            for it in items) + "\n\nhttps://blueliner.app/map\n",
    }
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(_RESEND_API, json=payload,
                       headers={"Authorization": f"Bearer {_API_KEY}"})
            r.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Resend digest failed for %s: %s", email, exc)
        return False
