"""
Favorite-water condition alerts (M4.1; digested + budgeted in M5.4).

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
- All of a user's transitions within one pass batch into a SINGLE
  digest email (M5.4a): N favorites blowing out in the same storm is
  one email, not N.
- Sends draw from daily budgets (M5.4b): a per-user cap (so a flappy
  gauge can't spam anyone) and a global cap sized under Resend's
  free-tier daily limit, with headroom reserved for magic-link sign-in
  emails -- login always outranks alerts. Exhausted budget skips the
  send but still records verdicts, so the transition isn't replayed
  when budget returns.
"""

import logging
import os

import db
import email_send

logger = logging.getLogger("blueliner.favorites")

# Transitions worth an email: arriving at actionable states.
_ALERT_STATES = {"green", "red"}

# Daily send budgets (M5.4b). The global default leaves ~10/day of
# Resend's 100/day free tier for magic links, which never check the
# budget. Per-user counts digests (emails), not transitions.
USER_DAILY_ALERT_CAP = int(os.environ.get("ALERT_USER_DAILY_CAP", "4"))
GLOBAL_DAILY_EMAIL_BUDGET = int(os.environ.get("EMAIL_DAILY_BUDGET", "90"))


def check_favorite_alerts(state: str, rivers: list[dict]) -> int:
    """Diff a state's fresh snapshot against its favorites; send one
    digest email per user covering their meaningful transitions.
    Returns the number of emails sent."""
    favs = db.favorites_for_state(state)
    if not favs:
        return 0
    by_site: dict[str, dict] = {}
    for r in rivers:
        site = r.get("site_no")
        if site:
            by_site[site] = r

    # Pass 1: record every verdict change; collect alertable
    # transitions per user for the digest.
    pending: dict[int, dict] = {}  # user_id -> {"email", "items"}
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
        entry = pending.setdefault(
            f["user_id"], {"email": f["email"], "items": []})
        entry["items"].append({"name": f["name"], "state": state,
                               "prev": prev, "new": overall})

    # Pass 2: one digest per user, budget-gated. User cap first (the
    # cheap, per-person check), then the shared global budget; when the
    # global budget is dry no further send can succeed today, so stop.
    sent = 0
    for user_id, entry in pending.items():
        if not db.try_spend_email_budget(
                f"alerts:user:{user_id}", USER_DAILY_ALERT_CAP):
            logger.info("alert cap hit for user %s; skipping digest",
                        user_id)
            continue
        if not db.try_spend_email_budget(
                "alerts:global", GLOBAL_DAILY_EMAIL_BUDGET):
            logger.warning(
                "global email budget exhausted; skipping remaining alerts")
            break
        if email_send.send_condition_digest(entry["email"], entry["items"]):
            sent += 1
    if sent:
        logger.info("favorite alerts %s: %d digest(s) sent", state, sent)
    return sent
