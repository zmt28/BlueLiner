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

logger = logging.getLogger("bluelines.email")

_RESEND_API = "https://api.resend.com/emails"
_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
# Display name + sending address. Keep the address on a domain whose
# SPF/DKIM/DMARC are configured to authorize Resend.
_FROM = os.environ.get("EMAIL_FROM", "BlueLines <no-reply@bluelines.app>")


def _build_magic_link_html(consume_url: str, expires_minutes: int) -> str:
    return (
        f'<div style="font-family:system-ui,sans-serif;max-width:480px;'
        f'margin:0 auto;padding:24px;color:#222">'
        f'<h2 style="margin:0 0 16px">Sign in to BlueLines</h2>'
        f'<p>Tap the button below to finish signing in. The link works '
        f'on this device for the next {expires_minutes} minutes and can '
        f'only be used once.</p>'
        f'<p style="margin:24px 0">'
        f'<a href="{consume_url}" style="display:inline-block;'
        f'background:#1e6fd9;color:#fff;text-decoration:none;'
        f'padding:12px 20px;border-radius:6px;font-weight:600">'
        f'Sign in to BlueLines</a></p>'
        f'<p style="font-size:13px;color:#666">If the button doesn\'t '
        f'work, copy and paste this URL:<br>'
        f'<span style="word-break:break-all">{consume_url}</span></p>'
        f'<p style="font-size:13px;color:#888;margin-top:24px">'
        f'Did not request this? You can safely ignore the email.</p>'
        f'</div>')


def send_magic_link(email: str, consume_url: str,
                    expires_minutes: int = 15) -> bool:
    """Send the sign-in link to `email`. Returns True on success.
    In dev (no API key) logs the URL and returns True so the flow
    can be tested locally by copy-pasting from the log."""
    if not _API_KEY:
        logger.info("(dev) magic-link for %s -> %s", email, consume_url)
        return True
    payload = {
        "from": _FROM,
        "to": [email],
        "subject": "Sign in to BlueLines",
        "html": _build_magic_link_html(consume_url, expires_minutes),
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
