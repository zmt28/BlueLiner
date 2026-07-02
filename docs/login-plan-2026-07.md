# Login & Identity Plan — July 2026 (M5)

Goal: make email a rare event instead of the toll booth for every
session, and make the sign-in flow feel as polished as the map. The
driver is cost — Resend is the only login path today and its free tier
(3,000 emails/mo, 100/day, as of mid-2026; the first paid tier is
$20/mo, which is most of the $25/mo total budget) — but every cost fix
below is also a UX fix: the cheapest email is the one a returning user
never needs.

## Where email is spent today

| Sender | Trigger | Volume shape |
| --- | --- | --- |
| Magic link (`email_send.send_magic_link`) | Every sign-in on every device | ≥1 per device per 30 days — the session cookie (`main._set_session_cookie`) is a fixed 30-day `max_age` with no renewal, so even daily users re-login monthly |
| Condition alerts (`email_send.send_condition_alert`) | Every favorite-river verdict transition into green/red | Unbounded per user: one email **per river per transition**. 3 favorites × ~4 transitions/mo each = 12 emails/user/mo |

Back-of-envelope: at 250 monthly users the alerts alone (~3,000/mo)
consume the entire free tier before a single login email is sent.
Logins add ~1.5 devices × 250 = ~375/mo. Alerts are the growth bomb;
login frequency is the recurring leak. The plan attacks both.

## Target end-state

- Returning user on a known device: **zero emails** — session never
  lapses while they keep visiting (M5.1), and a passkey re-auths them
  instantly if it does (M5.3).
- New device: one email — and the flow works even when the link is
  opened on a different device (M5.2 code fallback).
- Alerts: web push by default (free, M4.1b), email as opt-in fallback,
  digested and capped (M5.4).
- Email volume ≈ onboarding only. Free tier then supports thousands of
  users; no Resend paid tier, no SES migration needed.

---

## M5.1 — Session longevity (small; biggest recurring-email cut)

The 30-day fixed cookie is the leak: activity should extend the
session, and abandonment should end it server-side.

- [ ] **a. Sliding renewal.** `db.user_from_session` already touches
  `last_seen_at` on every validated request. Re-issue the cookie
  (same token, fresh 30-day `max_age`) when `last_seen_at` is >24 h
  old — a response-side check in `_session_user`'s callers or a small
  middleware. A user who opens the app monthly never re-logs-in.
- [ ] **b. Server-side idle expiry.** Sessions currently live forever
  in the DB (`user_from_session` checks nothing but existence).
  Reject + delete sessions idle >90 days; prune expired rows and
  used/expired `magic_links` rows in the precompute pass (same
  best-effort pattern as `favorites.check_favorite_alerts`).
- [ ] **c. Capture session context.** `main.auth_consume` calls
  `db.create_session(user["id"], sess_token, None, None)` — pass the
  real `User-Agent` and client IP. Costs nothing, enables M5.2f.

## M5.2 — Magic-link flow polish (medium; QoL)

- [ ] **a. 6-digit code fallback (cross-device fix).** The email
  gains a short code alongside the button; the "Check your inbox"
  step (`login-step-2` in `auth.ts`) gains a code input. Requesting
  on desktop and opening the email on a phone currently signs in the
  *phone*; typing the code signs in the tab that asked. Server:
  `magic_links` grows a hashed `code` column; `POST
  /api/auth/verify-code {email, code}` with a 5-attempt cap per link,
  minting the same session as `/auth/consume`.
- [ ] **b. Live "signed in" handoff.** While step-2 is visible, poll
  `/api/me` every ~3 s (cookie is browser-wide, so a link consumed in
  another tab of the same browser is visible immediately) — the modal
  flips to "You're in" and refreshes the auth slot without a manual
  reload. BroadcastChannel from the consume page as the fast path.
- [ ] **c. Resend cooldown + edit address.** The step-2 "try again"
  link becomes "Resend (60 s)" with a countdown, plus a separate
  "Wrong address?" that returns to step 1 with the field prefilled —
  today `openModal` blanks it.
- [ ] **d. Expired-link recovery.** `_consume_error_html` is a dead
  end ("request a fresh one" → start over). The `magic_links` row
  still holds the email even when expired: render a one-click "Send a
  new link to j***@gmail.com" button (masked, rate-limited through
  the existing `_rate_limit_auth` path).
- [ ] **e. Plain-text part + deliverability.** Both templates are
  HTML-only; add a `text` part (Resend supports it in the same
  payload) to lower spam scoring — a magic link in spam is a support
  ticket and a burned email.
- [ ] **f. Active-sessions list in Settings.** UA/IP/last-seen are in
  the `sessions` table once M5.1c lands; list devices with a revoke
  button (`DELETE /api/me/sessions/{id}`). Polish + a security
  feature users expect.

## M5.3 — Passkeys / WebAuthn (medium-large; kills login email on known devices)

Free forever, no third-party service, perfect PWA fit (Face ID /
fingerprint / device PIN). Library: `py_webauthn` (pure-Python deps).

- [ ] **a. Schema + endpoints.** `webauthn_credentials` table
  (user_id, credential_id, public_key, sign_count, transports,
  nickname, created_at, last_used_at). Four routes:
  `register-options` / `register` / `auth-options` / `authenticate`,
  session-minting identical to the magic-link path.
- [ ] **b. Post-sign-in enrollment prompt.** After a successful
  magic-link/code sign-in, one dismissable prompt: "Add a passkey and
  skip the email next time." (Same pattern as the pin-claim modal —
  `bl_passkey_dismissed` localStorage flag.)
- [ ] **c. Login modal integration.** "Sign in with a passkey" as the
  primary action when `PublicKeyCredential` is available, with
  conditional-mediation autofill on the email field
  (`autocomplete="username webauthn"`) so one tap signs in. Email
  flow stays as the fallback and the new-user path.
- [ ] **d. Manage passkeys in Settings.** List / rename / revoke.

## M5.4 — Alert email efficiency (small; protects the budget today)

- [ ] **a. Digest per pass.** `favorites.check_favorite_alerts`
  currently sends one email per river transition. Collect all
  transitions for a user within a refresh pass and send **one**
  email listing them. Cuts worst-case volume by the favorites count.
- [ ] **b. Caps.** Per-user daily alert cap (e.g. 4) and a global
  daily send counter (env `EMAIL_DAILY_BUDGET`, default 90 — just
  under Resend's 100/day free limit) checked before any send; when
  exhausted, skip alerts (never skip magic links — login outranks
  alerts for the remaining budget).
- [ ] **c. Web push (existing M4.1b).** The structural fix: push is
  free and instant. VAPID keypair (env vars on Render), `pywebpush`,
  push handler in `static/sw.js`, per-favorite channel preference
  defaulting to push with email as explicit opt-in. Once shipped,
  alert email drops to near zero and M5.4b caps become a dead-man
  switch rather than a daily governor.

## M5.5 — "Continue with Google" (optional; decide after M5.3)

One-tap OIDC covers most anglers' Gmail without any email send, and
costs nothing at runtime. But it adds a Google Cloud console
dependency, a consent screen, and a third credential path to
maintain. Recommendation: **defer** — ship passkeys first and check
adoption; if a meaningful share of users still lands on the email
flow for every new device, add Google then. (If added: `id_token`
verification server-side, reuse `upsert_user_by_email`, no new user
model needed.)

---

## Sequencing & effort

| Order | Item | Effort | Email impact |
| --- | --- | --- | --- |
| 1 | M5.4a+b digest + caps | ~half day | Caps the today-risk |
| 2 | M5.1 session longevity | ~half day | Kills the monthly re-login |
| 3 | M5.2 flow polish | ~1 day | QoL; code fallback also de-risks deliverability |
| 4 | M5.3 passkeys | ~2 days | Zero-email re-auth on known devices |
| 5 | M4.1b web push | ~1–2 days | Structural end of alert email |
| 6 | M5.5 Google | decide later | — |

Decisions needed from Zion:
- VAPID keys for M4.1b (I generate, you set two env vars on Render).
- `EMAIL_DAILY_BUDGET` default (proposed 90).
- Whether M5.5 (Google) is in or out of scope for now.
