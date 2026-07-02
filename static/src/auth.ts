/**
 * Authentication: magic-link sign-in, account menu, the modal
 * primitives every other module uses (openModal / closeModal), the
 * settings + delete-account flows, and the "claim anonymous pins"
 * prompt the user sees on first sign-in. Extracted from app.js in
 * PR B1j.
 *
 * Owns:
 *   - CURRENT_USER (module-private, exposed via getCurrentUser())
 *   - initAuth() async bootstrap (called from main.ts init)
 *   - loadAuthState(): fetches /api/me and refreshes the account UI
 *   - renderAuthSlot(): paints the rail avatar + mobile avatar + the
 *     profile pane (signed-in vs signed-out actions)
 *   - openModal/closeModal: the modal primitives (also exported on
 *     window so the legacy wireCatch + catch form code can call
 *     them without an import cycle)
 *   - toggleAccountMenu, wireAuthHandlers (login form + account-menu
 *     actions + claim-modal + settings handlers)
 *   - onAccountAction (logout, settings, my-catches)
 *   - maybePromptClaim + confirmClaim (post-sign-in anonymous-pin
 *     claim flow; respects bl_claim_dismissed localStorage flag)
 *   - loadSettings, saveDisplayName, deleteAccount
 *
 * Cross-module deps:
 *   - DEVICE_HEADER from state (for /api/pins/claimable + /claim)
 *   - loadPins from pins (refresh after a successful claim)
 *   - openMyCatches from catches (action="my-catches")
 */

import { DEVICE_HEADER } from "./state";
import { loadPins } from "./pins";
import { confirmDialog } from "./confirm";
import { showToast } from "./toast";

// -- CURRENT_USER state --------------------------------------------
// AuthMe-shaped value or null when signed out. Module-private; the
// getter below is the canonical read path.

let CURRENT_USER: AuthMe | null = null;

/** Returns the active signed-in user (null when anonymous). */
export function getCurrentUser(): AuthMe | null {
  return CURRENT_USER;
}

// -- Modal primitives -----------------------------------------------

export function openModal(id: string): void {
  const el = document.getElementById(id);
  if (!el) return;
  el.hidden = false;
  // Reset login modal state if reopened.
  if (id === "login-modal") {
    stopSignedInWatch();
    (document.getElementById("login-step-1") as HTMLElement).hidden = false;
    (document.getElementById("login-step-2") as HTMLElement).hidden = true;
    const inp = document.getElementById("login-email") as HTMLInputElement | null;
    if (inp) inp.value = "";
    const err = document.getElementById("login-error");
    if (err) err.hidden = true;
    setTimeout(() => inp && inp.focus(), 30);
  }
  if (id === "settings-modal") loadSettings();
}

export function closeModal(id: string): void {
  const el = document.getElementById(id);
  if (el) el.hidden = true;
}

// -- Auth state + header slot --------------------------------------

async function loadAuthState(): Promise<void> {
  try {
    const r = await fetch("/api/me");
    CURRENT_USER = r.ok ? ((await r.json()) as AuthMe) : null;
  } catch {
    CURRENT_USER = null;
  }
  renderAuthSlot();
}

/** Two-letter initials from a display name or email local-part. */
function userInitials(user: AuthMe): string {
  const raw =
    (user.display_name as string | undefined) ||
    (user.email as string | undefined) ||
    "";
  const base = raw.replace(/@.*/, "");
  const parts = base.split(/[\s._-]+/).filter(Boolean);
  const a = parts[0]?.[0] || "?";
  const b = parts.length > 1 ? parts[parts.length - 1][0] : "";
  return (a + b).toUpperCase();
}

/** Paint the rail account block, the mobile avatar, and the profile pane
 *  to reflect the signed-in / signed-out state. (Replaces the old header
 *  auth slot + account-menu dropdown.) */
function renderAuthSlot(): void {
  const signedIn = !!CURRENT_USER;
  const email = signedIn
    ? ((CURRENT_USER!.email as string | undefined) || "")
    : "";
  const name = signedIn
    ? ((CURRENT_USER!.display_name as string | undefined) || email)
    : "";
  const ini = signedIn ? userInitials(CURRENT_USER!) : "?";

  const setText = (id: string, text: string): void => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };
  const setHidden = (id: string, hidden: boolean): void => {
    const el = document.getElementById(id);
    if (el) el.hidden = hidden;
  };

  setText("rail-avatar", ini);
  setText("mobile-avatar", ini);
  setText("rail-email", signedIn ? email : "Sign in");
  setText("profile-avatar", ini);
  setText("profile-name", signedIn ? name : "Guest");
  setText("profile-email", signedIn ? email : "Not signed in");

  setHidden("profile-signin", signedIn);
  setHidden("profile-catches", !signedIn);
  setHidden("profile-settings", !signedIn);
  setHidden("profile-logout", !signedIn);
}

async function onAccountAction(action: string | undefined): Promise<void> {
  if (action === "signin") {
    openModal("login-modal");
    return;
  }
  if (action === "logout") {
    // Only reload once the server actually ended the session — reloading
    // on a failed request left the server session live while the UI
    // looked signed out.
    try {
      const r = await fetch("/api/auth/logout", { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      location.reload();
    } catch {
      showToast("Couldn't sign out — try again.", "error");
    }
  } else if (action === "settings") {
    openModal("settings-modal");
  } else if (action === "my-catches") {
    // openMyCatches is in catches.ts; reach via window to avoid an
    // import cycle (catches.ts imports openModal from this file).
    window.openMyCatches?.();
  }
}

// -- "Check your inbox" step (M5.2) ---------------------------------
// The step-2 pane owns three live behaviors: a signed-in watcher (the
// link may be consumed in another tab of this browser -- the cookie is
// browser-wide, so /api/me flips to 200 and we can finish without a
// manual reload), a resend cooldown, and the 6-digit code form for
// the cross-device case (email opened on the phone, session wanted
// here).

let lastLoginEmail = "";
let pollTimer: number | null = null;
let cooldownTimer: number | null = null;

function stopSignedInWatch(): void {
  if (pollTimer !== null) {
    window.clearInterval(pollTimer);
    pollTimer = null;
  }
}

function startSignedInWatch(): void {
  stopSignedInWatch();
  pollTimer = window.setInterval(async () => {
    const modal = document.getElementById("login-modal");
    const step2 = document.getElementById("login-step-2");
    if (!modal || modal.hidden || !step2 || step2.hidden) {
      stopSignedInWatch();
      return;
    }
    try {
      const r = await fetch("/api/me");
      if (r.ok) {
        stopSignedInWatch();
        location.reload(); // session cookie landed via another tab
      }
    } catch {
      /* offline blip; keep polling */
    }
  }, 3000);
}

// Fast path for the same-browser case: the consume page broadcasts on
// sign-in so this tab reacts instantly instead of on the next poll.
try {
  new BroadcastChannel("bl-auth").addEventListener("message", () => {
    const step2 = document.getElementById("login-step-2");
    if (step2 && !step2.hidden && !CURRENT_USER) location.reload();
  });
} catch {
  /* BroadcastChannel unsupported: polling covers it */
}

function startResendCooldown(seconds = 60): void {
  const btn = document.getElementById("login-resend") as HTMLButtonElement | null;
  if (!btn) return;
  if (cooldownTimer !== null) window.clearInterval(cooldownTimer);
  let left = seconds;
  btn.disabled = true;
  btn.textContent = `Resend link (${left}s)`;
  cooldownTimer = window.setInterval(() => {
    left -= 1;
    if (left <= 0) {
      window.clearInterval(cooldownTimer!);
      cooldownTimer = null;
      btn.disabled = false;
      btn.textContent = "Resend link";
    } else {
      btn.textContent = `Resend link (${left}s)`;
    }
  }, 1000);
}

async function requestLink(email: string): Promise<boolean> {
  try {
    const r = await fetch("/api/auth/request-link", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email }),
    });
    return r.ok;
  } catch {
    return false;
  }
}

function enterStep2(email: string): void {
  lastLoginEmail = email;
  (document.getElementById("login-sent-to") as HTMLElement).textContent = email;
  (document.getElementById("login-step-1") as HTMLElement).hidden = true;
  (document.getElementById("login-step-2") as HTMLElement).hidden = false;
  const code = document.getElementById("login-code") as HTMLInputElement | null;
  if (code) code.value = "";
  const err = document.getElementById("login-code-error");
  if (err) err.hidden = true;
  startResendCooldown();
  startSignedInWatch();
}

// -- Wire all auth-related DOM handlers ----------------------------

function wireAuthHandlers(): void {
  // Backdrop + [×] + data-close close their parent modal.
  document.querySelectorAll<HTMLElement>(".modal").forEach((m) => {
    m.querySelectorAll<HTMLElement>("[data-close]").forEach((b) =>
      b.addEventListener("click", () => {
        m.hidden = true;
      }),
    );
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll<HTMLElement>(".modal").forEach((m) => {
        m.hidden = true;
      });
    }
  });

  // Login form.
  const form = document.getElementById("login-form");
  if (form)
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const email = (document.getElementById("login-email") as HTMLInputElement)
        .value.trim();
      if (!email) return;
      const btn = document.getElementById("login-submit") as HTMLButtonElement;
      const errEl = document.getElementById("login-error") as HTMLElement | null;
      btn.disabled = true;
      btn.textContent = "Sending…";
      const sent = await requestLink(email);
      btn.disabled = false;
      btn.textContent = "Send sign-in link";
      // Only advance to "Check your inbox" when the link actually went
      // out -- a failed send used to strand the user waiting on an email
      // that was never sent.
      if (!sent) {
        if (errEl) {
          errEl.textContent =
            "Couldn't send the link — check your connection and try again.";
          errEl.hidden = false;
        }
        return;
      }
      if (errEl) errEl.hidden = true;
      enterStep2(email);
    });

  // 6-digit code form (cross-device sign-in).
  const codeForm = document.getElementById("login-code-form");
  if (codeForm)
    codeForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      const inp = document.getElementById("login-code") as HTMLInputElement;
      const code = inp.value.trim();
      const errEl = document.getElementById("login-code-error") as HTMLElement | null;
      if (!/^\d{6}$/.test(code)) {
        if (errEl) {
          errEl.textContent = "Enter the 6-digit code from the email.";
          errEl.hidden = false;
        }
        return;
      }
      const btn = document.getElementById("login-code-submit") as HTMLButtonElement;
      btn.disabled = true;
      btn.textContent = "Checking…";
      let ok = false;
      try {
        const r = await fetch("/api/auth/verify-code", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ email: lastLoginEmail, code }),
        });
        ok = r.ok;
      } catch {
        ok = false;
      }
      if (ok) {
        stopSignedInWatch();
        location.reload();
        return;
      }
      btn.disabled = false;
      btn.textContent = "Sign in";
      if (errEl) {
        errEl.textContent =
          "That code didn't match — double-check it, or resend the link.";
        errEl.hidden = false;
      }
      inp.select();
    });

  // Resend (cooldown-gated) + "wrong address" recovery.
  const resend = document.getElementById("login-resend");
  if (resend)
    resend.addEventListener("click", async () => {
      if (!lastLoginEmail) return;
      startResendCooldown();
      if (!(await requestLink(lastLoginEmail))) {
        showToast("Couldn't resend the link — try again.", "error");
      }
    });
  const editEmail = document.getElementById("login-edit-email");
  if (editEmail)
    editEmail.addEventListener("click", () => {
      stopSignedInWatch();
      (document.getElementById("login-step-2") as HTMLElement).hidden = true;
      (document.getElementById("login-step-1") as HTMLElement).hidden = false;
      const inp = document.getElementById("login-email") as HTMLInputElement;
      inp.value = lastLoginEmail; // prefill: usually a typo fix, not a restart
      inp.focus();
      inp.select();
    });

  // Account / profile actions (the profile pane replaces the old
  // account-menu dropdown).
  document
    .querySelectorAll<HTMLButtonElement>(".profile-action[data-action]")
    .forEach((b) => {
      b.addEventListener("click", () => onAccountAction(b.dataset.action));
    });

  // Claim modal.
  const claimBtn = document.getElementById("claim-confirm");
  if (claimBtn) claimBtn.addEventListener("click", confirmClaim);

  // Settings.
  const saveBtn = document.getElementById("settings-save");
  if (saveBtn) saveBtn.addEventListener("click", saveDisplayName);
  const delBtn = document.getElementById("settings-delete");
  if (delBtn) delBtn.addEventListener("click", deleteAccount);
}

// -- Anonymous-pin claim flow --------------------------------------

async function maybePromptClaim(): Promise<void> {
  if (!CURRENT_USER) return;
  if (localStorage.getItem("bl_claim_dismissed") === "1") return;
  try {
    const r = await fetch("/api/pins/claimable", { headers: DEVICE_HEADER });
    if (!r.ok) return;
    const list = (await r.json()) as Pin[];
    if (!list || !list.length) return;
    (document.getElementById("claim-count") as HTMLElement).textContent = String(list.length);
    const ul = document.getElementById("claim-list") as HTMLUListElement;
    ul.innerHTML = "";
    for (const p of list.slice(0, 6)) {
      const li = document.createElement("li");
      li.textContent = p.note || "(no note)";
      ul.appendChild(li);
    }
    if (list.length > 6) {
      const li = document.createElement("li");
      li.textContent = `… and ${list.length - 6} more`;
      ul.appendChild(li);
    }
    openModal("claim-modal");
    // Dismiss-on-skip applies even if the modal is closed with [×]/Esc;
    // no re-prompt for that device. Re-checks on next sign-in still
    // honor the persisted flag (per-device by design).
    document.getElementById("claim-modal")!.addEventListener(
      "click",
      (e) => {
        if ((e.target as HTMLElement).matches("[data-close]")) {
          localStorage.setItem("bl_claim_dismissed", "1");
        }
      },
      { once: true },
    );
  } catch {
    /* ignore */
  }
}

async function confirmClaim(): Promise<void> {
  const btn = document.getElementById("claim-confirm") as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = "Claiming…";
  let ok = false;
  try {
    const r = await fetch("/api/pins/claim", {
      method: "POST",
      headers: DEVICE_HEADER,
    });
    ok = r.ok;
  } catch {
    ok = false;
  }
  btn.disabled = false;
  btn.textContent = "Claim pins";
  if (!ok) {
    // Keep the modal open and DON'T set bl_claim_dismissed — a failed
    // claim used to look identical to success and was never re-offered.
    showToast("Couldn't claim your pins — try again.", "error");
    return;
  }
  localStorage.setItem("bl_claim_dismissed", "1");
  closeModal("claim-modal");
  showToast("Pins claimed", "success");
  loadPins();
}

// -- Settings ------------------------------------------------------

function loadSettings(): void {
  if (!CURRENT_USER) return;
  (document.getElementById("settings-email") as HTMLElement).textContent =
    (CURRENT_USER.email as string | undefined) || "";
  (document.getElementById("settings-name") as HTMLInputElement).value =
    (CURRENT_USER.display_name as string | undefined) || "";
  (document.getElementById("settings-saved") as HTMLElement).style.opacity = "0";
  void loadSessionsList();
}

// -- Active devices (M5.2f) -----------------------------------------

/** "Chrome on Windows" from a UA string; terse on purpose. */
function summarizeUa(ua: string | null): string {
  if (!ua) return "Unknown device";
  const os = /iPhone|iPad/.test(ua) ? "iPhone/iPad"
    : /Android/.test(ua) ? "Android"
    : /Mac OS X|Macintosh/.test(ua) ? "Mac"
    : /Windows/.test(ua) ? "Windows"
    : /Linux/.test(ua) ? "Linux" : "";
  const browser = /Edg\//.test(ua) ? "Edge"
    : /Firefox\//.test(ua) ? "Firefox"
    : /Chrome\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari" : "Browser";
  return os ? `${browser} on ${os}` : browser;
}

function relTime(iso: string | null): string {
  if (!iso) return "a while ago";
  const ms = Date.now() - new Date(iso).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "just now";
  const min = Math.floor(ms / 60_000);
  if (min < 2) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.floor(h / 24)} days ago`;
}

async function loadSessionsList(): Promise<void> {
  const ul = document.getElementById("settings-sessions") as HTMLUListElement | null;
  if (!ul) return;
  ul.innerHTML = "<li class='sess-note'>Loading…</li>";
  interface Sess {
    id: string;
    last_seen_at: string | null;
    user_agent: string | null;
    current: boolean;
  }
  let sessions: Sess[];
  try {
    const r = await fetch("/api/me/sessions");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    sessions = ((await r.json()) as { sessions: Sess[] }).sessions;
  } catch {
    ul.innerHTML = "<li class='sess-note'>Couldn't load devices.</li>";
    return;
  }
  ul.innerHTML = "";
  for (const s of sessions) {
    const li = document.createElement("li");
    li.className = "sess-row";
    const meta = document.createElement("div");
    meta.className = "sess-meta";
    const label = document.createElement("div");
    label.textContent = summarizeUa(s.user_agent)
      + (s.current ? " — this device" : "");
    const seen = document.createElement("div");
    seen.className = "sess-seen";
    seen.textContent = `Last active ${relTime(s.last_seen_at)}`;
    meta.append(label, seen);
    li.appendChild(meta);
    if (!s.current) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "secondary sess-revoke";
      btn.textContent = "Sign out";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          const r = await fetch(
            `/api/me/sessions/${encodeURIComponent(s.id)}`,
            { method: "DELETE" });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          li.remove();
          showToast("Device signed out", "success");
        } catch {
          btn.disabled = false;
          showToast("Couldn't sign that device out — try again.", "error");
        }
      });
      li.appendChild(btn);
    }
    ul.appendChild(li);
  }
}

async function saveDisplayName(): Promise<void> {
  const name = (document.getElementById("settings-name") as HTMLInputElement)
    .value.trim();
  if (!name) return;
  const btn = document.getElementById("settings-save") as HTMLButtonElement;
  btn.disabled = true;
  try {
    const r = await fetch("/api/me", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ display_name: name }),
    });
    if (r.ok) {
      CURRENT_USER = (await r.json()) as AuthMe;
      renderAuthSlot();
      const t = document.getElementById("settings-saved") as HTMLElement;
      t.style.opacity = "1";
      setTimeout(() => {
        t.style.opacity = "0";
      }, 1400);
    } else {
      showToast("Couldn't save your name — try again.", "error");
    }
  } catch {
    showToast("Couldn't save your name — check your connection.", "error");
  }
  btn.disabled = false;
}

async function deleteAccount(): Promise<void> {
  const ok = await confirmDialog({
    title: "Delete your account?",
    message:
      "Pins you've claimed will become anonymous again on this device. " +
      "This cannot be undone.",
    confirmLabel: "Delete account",
    danger: true,
  });
  if (!ok) return;
  try {
    await fetch("/api/me", { method: "DELETE" });
  } catch {
    /* ignore */
  }
  localStorage.removeItem("bl_claim_dismissed");
  location.reload();
}

// -- Async bootstrap (called from main.ts init) --------------------

export async function initAuth(): Promise<void> {
  await loadAuthState();
  wireAuthHandlers();
  // catches.ts wires its own form + my-catches DOM at module init;
  // initAuth() doesn't need to trigger it.
  if (CURRENT_USER) await maybePromptClaim();
}

// -- Window bridge for cross-module consumers ----------------------
// openModal + closeModal exposed so the legacy wireCatch (still
// reachable via the river panel) can call them without a circular
// import. catches.ts's `openMyCatches` is bridged the same way (set
// from catches.ts) so onAccountAction("my-catches") can dispatch.

declare global {
  interface Window {
    openModal: typeof openModal;
    closeModal: typeof closeModal;
    initAuth: typeof initAuth;
    getCurrentUser: typeof getCurrentUser;
    // openMyCatches declared canonically in catches.ts.
  }
}

window.openModal = openModal;
window.closeModal = closeModal;
window.initAuth = initAuth;
window.getCurrentUser = getCurrentUser;
