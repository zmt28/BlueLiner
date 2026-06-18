"""Proactive mode: watch a user's saved rivers and alert on "comes into shape".

Designed to run on the existing GitHub Actions cron (refresh-precompute.yml
style). On each tick, for every watched river it pulls current conditions, runs
the deterministic scorer + the same guardrail block rules, and when a river
crosses from NOT-ideal -> ideal it emails the angler via Resend.

Autonomy boundary (the human-in-the-loop line for the deck): this agent decides
WHEN to notify on its own, but it ONLY notifies -- it never books, posts, or
changes any user data. Ideal = scorer rates it green AND it passes every
guardrail AND the reading is fresh.

Run:
  python -m agent.watch            # one tick over the demo watchlist
  python -m agent.watch --demo     # force a transition to capture a sample alert
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import httpx

from . import config, datasources

STATE_PATH = config.LOG_DIR / "watch_state.json"

# Demo watchlist: in production this comes from each signed-in user's saved pins
# / rivers. (email -> river_ids)
DEFAULT_WATCHLIST = {
    "demo-angler@blueliner.app": [
        "north-branch-potomac-md", "savage-river-md", "gunpowder-falls-md",
    ],
}


def _is_ideal(river_id: str) -> tuple[bool, dict]:
    cond = datasources.get_river_conditions(river_id)
    access = datasources.get_access(river_id)
    score = cond.get("score", {})
    ratio = score.get("flow_ratio")
    temp = cond.get("water_temp_f")
    stale = (cond.get("last_updated_hours_ago") or 0) > config.STALE_HOURS
    ideal = (
        cond.get("rating") == "green"
        and access.get("public_access", False)
        and (ratio is None or ratio <= config.FLOOD_RATIO)
        and (temp is None or temp <= config.TEMP_MAX_F)
        and not stale
    )
    return ideal, {**cond, "public_access": access.get("public_access")}


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except OSError:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _compose(cond: dict) -> tuple[str, str]:
    name = cond.get("name", cond["river_id"])
    ratio = (cond.get("score") or {}).get("flow_ratio")
    temp = cond.get("water_temp_f")
    bits = []
    if ratio is not None:
        bits.append(f"flow {ratio:.1f}x median")
    if temp is not None:
        bits.append(f"water {temp:.0f}F")
    detail = ", ".join(bits) or "conditions in range"
    subject = f"{name} just hit ideal"
    body = (f"{name} just came into shape: {detail}. Public access, fresh reading. "
            f"Source: {cond.get('source')}.\n\n"
            f"— Blueliner (you're getting this because {name} is on your watchlist)")
    return subject, body


def _send_alert(email: str, subject: str, body: str) -> None:
    """Resend send; dev mode (no key) logs the alert -- the autonomy boundary
    means we only ever NOTIFY."""
    api_key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("EMAIL_FROM", "Blueliner <no-reply@blueliner.app>")
    if not api_key:
        print(f"[watch][dev-email] to={email}\n  subject: {subject}\n  {body}\n")
        return
    try:
        httpx.post("https://api.resend.com/emails", timeout=10.0,
                   headers={"Authorization": f"Bearer {api_key}"},
                   json={"from": sender, "to": [email], "subject": subject,
                         "text": body})
        print(f"[watch] alert emailed to {email}: {subject}")
    except Exception as exc:
        print(f"[watch] email failed for {email}: {exc}")


def tick(watchlist: dict | None = None, force_transition: bool = False) -> int:
    watchlist = watchlist or DEFAULT_WATCHLIST
    state = _load_state()
    sent = 0
    for email, rivers in watchlist.items():
        for rid in rivers:
            ideal, cond = _is_ideal(rid)
            key = f"{email}:{rid}"
            prev_ideal = state.get(key, {}).get("ideal", False)
            if force_transition:
                prev_ideal = False  # pretend it was not-ideal to demo the alert
            if ideal and not prev_ideal:
                subject, body = _compose(cond)
                _send_alert(email, subject, body)
                sent += 1
            state[key] = {"ideal": ideal,
                          "checked_at": datetime.now(timezone.utc).isoformat()}
    _save_state(state)
    print(f"[watch] checked {sum(len(v) for v in watchlist.values())} rivers; "
          f"{sent} ideal-transition alert(s) sent.")
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="force a not-ideal->ideal transition to capture a sample alert")
    args = ap.parse_args()
    tick(force_transition=args.demo)


if __name__ == "__main__":
    main()
