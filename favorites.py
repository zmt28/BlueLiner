"""
Favorite-water condition alerts (M4.1).

Pure glue between the precompute pass and the datastore/email: after a
state's river snapshot lands, `check_favorite_alerts` diffs each
favorite's fresh verdict against its stored `last_overall` and emails
on meaningful transitions. Runs inside the precompute cycle (never on
the request path), so a slow Resend call can't block a user.

Alert policy:
- The FIRST observation of a favorite (last_overall NULL) records the
  verdict silently -- favoriting a green river shouldn't email you that
  it's green.
- Only transitions INTO green ("grab your rod") or INTO red ("don't
  bother driving") alert; yellow/gray transitions just update state.
- `notify` off => state still updates, no email (so re-enabling alerts
  later doesn't replay stale transitions).
"""

import logging

import db
import email_send

logger = logging.getLogger("blueliner.favorites")

# Transitions worth an email: arriving at actionable states.
_ALERT_STATES = {"green", "red"}


def check_favorite_alerts(state: str, rivers: list[dict]) -> int:
    """Diff a state's fresh snapshot against its favorites; email on
    meaningful transitions. Returns the number of alerts sent."""
    favs = db.favorites_for_state(state)
    if not favs:
        return 0
    by_site: dict[str, dict] = {}
    for r in rivers:
        site = r.get("site_no")
        if site:
            by_site[site] = r
    sent = 0
    for f in favs:
        river = by_site.get(f["site_no"])
        if not river:
            continue  # gauge absent this pass; keep prior state
        overall = (river.get("conditions") or {}).get("overall") or "gray"
        prev = f.get("last_overall")
        if prev == overall:
            continue
        db.set_favorite_verdict(f["user_id"], f["site_no"], overall)
        if prev is None or not f.get("notify"):
            continue
        if overall not in _ALERT_STATES:
            continue
        if email_send.send_condition_alert(
                f["email"], f["name"], state, prev, overall):
            sent += 1
    if sent:
        logger.info("favorite alerts %s: %d sent", state, sent)
    return sent
