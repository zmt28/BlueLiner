// @ts-nocheck
// `tsconfig.json` enables `checkJs` so types.d.ts and the (PR B) TS
// modules get strict validation. This single-file legacy app.js opts
// OUT for now -- it uses untyped document.getElementById /
// querySelector in ~70 places, all of which would need per-line
// JSDoc casts. PR B splits this file into typed TS modules where the
// narrowing happens naturally via typed helpers (e.g.
// `byId<HTMLInputElement>("...")`). Until then, the type contract
// surface that matters (types.d.ts) is what `npm run typecheck`
// validates, plus sw.js which IS @ts-check'd.
"use strict";

// All state-related plumbing -- STATES catalog, currentSt code,
// deviceToken / DEVICE_HEADER / STATE_ZOOM / currentState() helpers --
// owned by static/src/state.ts since PR B1i. App.js reads via window
// (e.g. window.STATES[code].center inside init).
const STATE_ZOOM = window.STATE_ZOOM;
const DEVICE_HEADER = window.DEVICE_HEADER;
const deviceToken = window.deviceToken;
const esc = window.esc;
const popupOpts = window.popupOpts;

// Map (singleton) + the pins layer container -- this file still
// owns the saved-pin marker creation (B1j scope).
const map = window.map;
const pinsLayer = window.pinsLayer;

// Rivers loader -- still called from init() below.
const loadRivers = window.loadRivers;

// -- 1-yr USGS trend sparkline -- extracted to static/src/sparkline.ts
// in PR B1c. Re-exposed here so existing call sites resolve.
const sparkline = window.sparkline;
const wireSparkHover = window.wireSparkHover;

// Wire each gauge's on-demand "show flow trend" button within `root`
// (the river detail panel body). The primary gauge's chart is loaded
// eagerly elsewhere; this covers secondary gauges.
function wireTrend(root) {
  if (!root) return;
  root.querySelectorAll(".bl-trend-btn").forEach((btn) => {
    if (btn.dataset.wired) return;
    btn.dataset.wired = "1";
    const site = btn.getAttribute("data-site");
    const box = root.querySelector(`.bl-trend[data-site="${site}"]`);
    btn.onclick = async () => {
      btn.disabled = true;
      if (box) box.innerHTML = '<div class="bl-trend-msg">Loading 1-yr trend&hellip;</div>';
      try {
        const d = await fetch(
          `/api/history?site_no=${encodeURIComponent(site)}`
        ).then((r) => r.json());
        if (box) { box.innerHTML = sparkline(d.series); wireSparkHover(box); }
      } catch (_) {
        if (box) box.innerHTML = '<div class="bl-trend-msg">Trend unavailable.</div>';
      }
      btn.style.display = "none";
    };
  });
}

// Inject the "Log a catch" CTA into the detail panel `root`, wired to
// `river`. Signed-out users get a sign-in nudge instead.
function wireCatch(root, river) {
  if (!root || !river) return;
  let slot = root.querySelector(".bl-catch-cta");
  if (!slot) {
    // Older cached popup HTML without the placeholder: append one.
    slot = document.createElement("div");
    slot.className = "bl-catch-cta";
    root.appendChild(slot);
  }
  if (slot.dataset.wired) return;
  slot.dataset.wired = "1";

  if (CURRENT_USER) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bl-catch-btn";
    btn.textContent = "🎣 Log a catch here";
    btn.onclick = () => openCatchForm(river);
    slot.appendChild(btn);
  } else {
    const a = document.createElement("button");
    a.type = "button";
    a.className = "bl-catch-signin";
    a.textContent = "Sign in to log catches";
    a.onclick = () => openModal("login-modal");
    slot.appendChild(a);
  }
}
// Bridges for river-panel.ts's openRiverPanel(), which calls these
// to wire the gauge-trend buttons + the catch-log CTA inside the
// freshly-injected panel body. Future PRs that fully extract these
// (wire-trend.ts / catches.ts) drop the window assignments.
window.wireTrend = wireTrend;
window.wireCatch = wireCatch;

// -- River detail panel -- extracted to static/src/river-panel.ts +
// static/src/snap-sheet.ts in PR B1f. Re-exposed via window so the
// rivers / streams / lines code throughout this file can still call
// openRiverPanel / closeRiverPanel / highlightRiver / etc. by bare
// name. The ungauged-stream card path (onStreamClick further down)
// uses prepareRiverPanel + commitRiverPanelOpen to share the panel-
// opening primitives without duplicating timer + scroll + animation
// logic. PR B1g (streams.ts) moves onStreamClick out of here and
// can drop the prepareRiverPanel / commitRiverPanelOpen window
// indirection in favor of direct ES imports.
const refreshIcons = window.refreshIcons;
const openRiverPanel = window.openRiverPanel;
const closeRiverPanel = window.closeRiverPanel;
const prepareRiverPanel = window.prepareRiverPanel;
const commitRiverPanelOpen = window.commitRiverPanelOpen;
const highlightRiver = window.highlightRiver;
const clearRiverHighlight = window.clearRiverHighlight;
const autoLoadFlowChart = window.autoLoadFlowChart;
const wireRiverPanel = window.wireRiverPanel;
const wireSnapSheet = window.wireSnapSheet;

// -- Clickable-stream network -- entire block (style helpers, viewport
// fetcher, highlight state machine, _gaugedRiverFor, onStreamClick)
// extracted to static/src/streams.ts in PR B1g. Re-exposed here so the
// remaining app.js code (controls-panel segment buttons, the moveend
// debouncer, renderRivers) can keep calling by bare name. The
// streamColorMode setter is intentionally a function call (not a
// rebind) -- a `let X = window.X` for a primitive would freeze on
// the initial value rather than tracking later mutations from
// streams.ts.
const streamColor = window.streamColor;
const streamStyle = window.streamStyle;
const restyleStreams = window.restyleStreams;
const loadClickableStreams = window.loadClickableStreams;
const _riverHasClickableReach = window._riverHasClickableReach;
const highlightStream = window.highlightStream;
const clearStreamHighlight = window.clearStreamHighlight;
const onStreamClick = window.onStreamClick;
const setStreamColorMode = window.setStreamColorMode;

// Color-mode segmented control, state selector, filter handlers,
// layer-toggle wiring, controls panel open/close + tabs, base-map
// segment + reset-filters button -- all extracted to
// static/src/controls.ts in PR B1i.


// -- Gauges + filters + render + line fetch (populateHatchOptions,
// riverPasses, renderRivers, CONDITION_VARIANT, makeConditionIcon,
// the per-site + bulk river-line fetchers, viewport vs state mode
// switching, loadRivers, scheduleLazyRetry, the moveend listener)
// -- all extracted to static/src/rivers.ts in PR B1h. Re-exposed
// via window above.

// Trout streams cover the whole state now (large). Load lazily -- only
// when the user toggles the layer on -- and once per state, so the
// initial map (layer off by default) is never blocked by a multi-MB
// GeoJSON parse.
// ensureTrout / ensureAccess / loadPublicLands / makeAccessIcon /
// accessPopupHtml all moved to static/src/map-layers.ts in PR B1e.
// Re-exposed at the top of this file via window rebinds.


// -- Saved pins --

function addPinMarker(p) {
  const icon = L.divIcon({
    className: "bl-pin",
    html: '<div class="bl-pin-dot"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 16],
  });
  const m = L.marker([p.lat, p.lon], { icon });
  m.bindPopup(
    `<div class="pin-popup"><div class="pin-note">${esc(p.note || "(no note)")}</div>` +
    `<div class="pin-meta">${esc(p.created_at)}</div>` +
    `<button class="pin-del" type="button">Delete</button></div>`,
    popupOpts()
  );
  m.on("popupopen", (e) => {
    const btn = e.popup.getElement().querySelector(".pin-del");
    if (btn) {
      btn.onclick = async () => {
        await fetch(`/api/pins/${p.id}`, { method: "DELETE", headers: DEVICE_HEADER });
        pinsLayer.removeLayer(m);
        map.closePopup();
      };
    }
  });
  pinsLayer.addLayer(m);
}

async function loadPins() {
  const pins = await fetch("/api/pins", { headers: DEVICE_HEADER }).then((r) => r.json());
  pinsLayer.clearLayers();
  (pins || []).forEach(addPinMarker);
}

// -- Drop-a-pin interaction --

let pinMode = false;
let pendingLatLng = null;
const dropBtn = document.getElementById("drop-pin");
const pinForm = document.getElementById("pin-form");
const pinNote = document.getElementById("pin-note");

function setPinMode(on) {
  pinMode = on;
  dropBtn.classList.toggle("active", on);
  map.getContainer().style.cursor = on ? "crosshair" : "";
}

dropBtn.onclick = () => setPinMode(!pinMode);

map.on("click", (e) => {
  if (!pinMode) return;
  pendingLatLng = e.latlng;
  pinNote.value = "";
  pinForm.hidden = false;
  pinNote.focus();
});

document.getElementById("pin-cancel").onclick = () => {
  pinForm.hidden = true;
  pendingLatLng = null;
};

document.getElementById("pin-save").onclick = async () => {
  if (!pendingLatLng) return;
  const res = await fetch("/api/pins", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...DEVICE_HEADER },
    body: JSON.stringify({
      lat: pendingLatLng.lat,
      lon: pendingLatLng.lng,
      note: pinNote.value,
    }),
  });
  if (res.ok) {
    addPinMarker(await res.json());
  }
  pinForm.hidden = true;
  pendingLatLng = null;
  setPinMode(false);
};

// Filter / state-selector / controls-panel / layer-toggle / base-map
// segment / reset-filters wiring all extracted to static/src/controls.ts
// in PR B1i.

// -- Init --

async function init() {
  const list = await fetch("/api/states").then((r) => r.json());
  // Populate the canonical state.ts catalog (mutates in place; window.STATES
  // reference stays valid). The state-selector dropdown is filled here
  // because it's a DOM node owned by index.html; the rest of state.ts
  // doesn't care about the DOM.
  window.setStates(list);
  const sel = document.getElementById("state-select");
  sel.innerHTML = "";
  for (const s of list) {
    const opt = document.createElement("option");
    opt.value = s.code;
    opt.textContent = s.name;
    sel.appendChild(opt);
  }
  const state = window.currentState();
  // Init reads the active code from the URL, so syncing it back into
  // the URL is a no-op -- skip via syncUrl:false.
  window.setCurrentSt(state, { syncUrl: false });
  sel.value = state;
  map.setView(window.STATES[state].center, STATE_ZOOM);
  wireRiverPanel();
  loadRivers(state);
  loadPins();
  await initAuth();
}
init();

// Hydrate the static <i data-lucide="..."> nodes in the page shell
// (header tab buttons, sign-in mailbox). Wrapped in load so the
// deferred CDN script has finished parsing before we call into it.
window.addEventListener("load", refreshIcons);

// -- Accounts (Phase 1) ---------------------------------------------

// Auth state derived from /api/me on load. null = signed out.
let CURRENT_USER = null;

async function initAuth() {
  await loadAuthState();
  wireAuthHandlers();
  wireCatchUI();
  if (CURRENT_USER) await maybePromptClaim();
}

async function loadAuthState() {
  try {
    const r = await fetch("/api/me");
    CURRENT_USER = r.ok ? await r.json() : null;
  } catch {
    CURRENT_USER = null;
  }
  renderAuthSlot();
}

function renderAuthSlot() {
  const slot = document.getElementById("auth-slot");
  if (!slot) return;
  slot.innerHTML = "";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "ctrl";
  if (CURRENT_USER) {
    btn.id = "account-btn";
    btn.textContent =
      (CURRENT_USER.display_name || CURRENT_USER.email) + " ▾";
    btn.addEventListener("click", toggleAccountMenu);
  } else {
    btn.id = "signin-btn";
    btn.textContent = "Sign in";
    btn.addEventListener("click", () => openModal("login-modal"));
  }
  slot.appendChild(btn);
}

function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.hidden = false;
  // Reset login modal state if reopened
  if (id === "login-modal") {
    document.getElementById("login-step-1").hidden = false;
    document.getElementById("login-step-2").hidden = true;
    const inp = document.getElementById("login-email");
    if (inp) inp.value = "";
    setTimeout(() => inp && inp.focus(), 30);
  }
  if (id === "settings-modal") loadSettings();
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.hidden = true;
}

function toggleAccountMenu() {
  const menu = document.getElementById("account-menu");
  if (!menu) return;
  const showing = !menu.hidden;
  menu.hidden = showing;
  if (!showing) {
    document.getElementById("account-menu-email").textContent =
      CURRENT_USER ? CURRENT_USER.email : "";
  }
}

function wireAuthHandlers() {
  // Backdrop + [×] + data-close close their parent modal
  document.querySelectorAll(".modal").forEach((m) => {
    m.querySelectorAll("[data-close]").forEach((b) =>
      b.addEventListener("click", () => (m.hidden = true)));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      document.querySelectorAll(".modal").forEach((m) => (m.hidden = true));
      const menu = document.getElementById("account-menu");
      if (menu) menu.hidden = true;
    }
  });
  // Close account menu on outside click
  document.addEventListener("click", (e) => {
    const menu = document.getElementById("account-menu");
    if (!menu || menu.hidden) return;
    if (e.target.closest("#account-menu") ||
        e.target.closest("#account-btn")) return;
    menu.hidden = true;
  });

  // Login form
  const form = document.getElementById("login-form");
  if (form) form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("login-email").value.trim();
    if (!email) return;
    const btn = document.getElementById("login-submit");
    btn.disabled = true;
    btn.textContent = "Sending…";
    try {
      await fetch("/api/auth/request-link", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email }),
      });
    } catch {}
    btn.disabled = false;
    btn.textContent = "Send sign-in link";
    document.getElementById("login-sent-to").textContent = email;
    document.getElementById("login-step-1").hidden = true;
    document.getElementById("login-step-2").hidden = false;
  });
  const retry = document.getElementById("login-retry");
  if (retry) retry.addEventListener("click", () => {
    document.getElementById("login-step-2").hidden = true;
    document.getElementById("login-step-1").hidden = false;
    document.getElementById("login-email").focus();
  });

  // Account menu actions
  document.querySelectorAll("#account-menu button").forEach((b) => {
    b.addEventListener("click", () => onAccountAction(b.dataset.action));
  });

  // Claim modal
  const claimBtn = document.getElementById("claim-confirm");
  if (claimBtn) claimBtn.addEventListener("click", confirmClaim);

  // Settings
  const saveBtn = document.getElementById("settings-save");
  if (saveBtn) saveBtn.addEventListener("click", saveDisplayName);
  const delBtn = document.getElementById("settings-delete");
  if (delBtn) delBtn.addEventListener("click", deleteAccount);
}

async function onAccountAction(action) {
  document.getElementById("account-menu").hidden = true;
  if (action === "logout") {
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } catch {}
    location.reload();
  } else if (action === "settings") {
    openModal("settings-modal");
  } else if (action === "my-catches") {
    openMyCatches();
  }
}

async function maybePromptClaim() {
  if (!CURRENT_USER) return;
  if (localStorage.getItem("bl_claim_dismissed") === "1") return;
  try {
    const r = await fetch("/api/pins/claimable", {
      headers: DEVICE_HEADER,
    });
    if (!r.ok) return;
    const list = await r.json();
    if (!list || !list.length) return;
    document.getElementById("claim-count").textContent = list.length;
    const ul = document.getElementById("claim-list");
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
    document.getElementById("claim-modal").addEventListener("click", (e) => {
      if (e.target.matches("[data-close]")) {
        localStorage.setItem("bl_claim_dismissed", "1");
      }
    }, { once: true });
  } catch {}
}

async function confirmClaim() {
  const btn = document.getElementById("claim-confirm");
  btn.disabled = true;
  btn.textContent = "Claiming…";
  try {
    await fetch("/api/pins/claim", {
      method: "POST",
      headers: DEVICE_HEADER,
    });
    localStorage.setItem("bl_claim_dismissed", "1");
  } catch {}
  closeModal("claim-modal");
  loadPins();
}

async function loadSettings() {
  if (!CURRENT_USER) return;
  document.getElementById("settings-email").textContent = CURRENT_USER.email;
  document.getElementById("settings-name").value =
    CURRENT_USER.display_name || "";
  document.getElementById("settings-saved").style.opacity = 0;
}

async function saveDisplayName() {
  const name = document.getElementById("settings-name").value.trim();
  if (!name) return;
  const btn = document.getElementById("settings-save");
  btn.disabled = true;
  try {
    const r = await fetch("/api/me", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ display_name: name }),
    });
    if (r.ok) {
      CURRENT_USER = await r.json();
      renderAuthSlot();
      const t = document.getElementById("settings-saved");
      t.style.opacity = 1;
      setTimeout(() => (t.style.opacity = 0), 1400);
    }
  } catch {}
  btn.disabled = false;
}

async function deleteAccount() {
  if (!confirm(
    "Delete your account? Pins you've claimed will become anonymous " +
    "again on this device. This cannot be undone."
  )) return;
  try {
    await fetch("/api/me", { method: "DELETE" });
  } catch {}
  localStorage.removeItem("bl_claim_dismissed");
  location.reload();
}

// -- Catch log (Phase 2) --------------------------------------------

const SPECIES = [
  "Brown Trout", "Rainbow Trout", "Brook Trout", "Cutthroat Trout",
  "Tiger Trout", "Smallmouth Bass", "Largemouth Bass", "Bluegill",
  "Carp", "Fallfish", "Chain Pickerel", "Walleye",
];

// Context for the form: which river it was launched from (drives the
// enrichment lat/lon/site_no even if the user edits the river name).
let catchCtx = null;

function _toLocalInputValue(d) {
  // datetime-local wants "YYYY-MM-DDTHH:MM" in *local* time.
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().slice(0, 16);
}

function openCatchForm(river) {
  catchCtx = {
    river_name: river.name || "",
    river_site_no: river.site_no || null,
    lat: river.lat, lon: river.lon,
  };
  // Populate species datalist once
  const dl = document.getElementById("cf-species-list");
  if (dl && !dl.dataset.filled) {
    dl.dataset.filled = "1";
    for (const s of SPECIES) {
      const o = document.createElement("option");
      o.value = s;
      dl.appendChild(o);
    }
  }
  document.getElementById("catch-form").reset();
  document.getElementById("cf-river").value = catchCtx.river_name;
  document.getElementById("cf-when").value = _toLocalInputValue(new Date());
  document.getElementById("cf-error").textContent = "";
  openModal("catch-modal");
  loadEnrichmentPreview();
}

async function loadEnrichmentPreview() {
  const body = document.getElementById("cf-enrich-body");
  body.innerHTML = '<div class="cf-enrich-loading">Reading current conditions&hellip;</div>';
  if (!catchCtx || catchCtx.lat == null || catchCtx.lon == null) {
    body.innerHTML = '<div class="cf-enrich-loading">No location — conditions won’t be captured.</div>';
    return;
  }
  const p = new URLSearchParams({ lat: catchCtx.lat, lon: catchCtx.lon });
  if (catchCtx.river_site_no) p.set("site_no", catchCtx.river_site_no);
  if (catchCtx.river_name) p.set("river_name", catchCtx.river_name);
  const when = document.getElementById("cf-when").value;
  if (when) p.set("occurred_at", new Date(when).toISOString());
  try {
    const env = await fetch(`/api/catches/enrichment-preview?${p}`).then((r) => r.json());
    body.innerHTML = renderEnv(env);
  } catch {
    body.innerHTML = '<div class="cf-enrich-loading">Conditions unavailable right now.</div>';
  }
}

function renderEnv(env) {
  if (!env) return '<div class="cf-enrich-loading">No conditions.</div>';
  const rows = [];
  const flow = env.flow_cfs != null
    ? `${env.flow_cfs} cfs${env.flow_vs_median ? " (" + esc(env.flow_vs_median) + ")" : ""}`
    : null;
  if (flow) rows.push(["💧", "Flow", flow]);
  if (env.water_temp_f != null) rows.push(["🌡", "Water", `${env.water_temp_f}°F`]);
  if (env.air_temp_f != null) {
    rows.push(["☁", "Air", `${env.air_temp_f}°F${env.conditions ? ", " + esc(env.conditions) : ""}`]);
  }
  if (env.pressure_inhg != null) rows.push(["📊", "Pressure", `${env.pressure_inhg} inHg`]);
  if (env.moon_phase) rows.push(["🌙", "Moon", esc(env.moon_phase)]);
  if (env.active_hatches && env.active_hatches.length) {
    rows.push(["🦟", "Hatches", env.active_hatches.map(esc).join(", ")]);
  }
  if (!rows.length) return '<div class="cf-enrich-loading">No conditions captured for this spot.</div>';
  return rows.map((r) =>
    `<div class="cf-env-row"><span class="cf-env-ic">${r[0]}</span>` +
    `<span class="cf-env-k">${r[1]}</span><span class="cf-env-v">${r[2]}</span></div>`
  ).join("");
}

async function submitCatch(ev) {
  ev.preventDefault();
  const species = document.getElementById("cf-species").value.trim();
  const err = document.getElementById("cf-error");
  if (!species) { err.textContent = "Species is required."; return; }
  const lenRaw = document.getElementById("cf-length").value;
  const whenRaw = document.getElementById("cf-when").value;
  const payload = {
    species,
    river_name: document.getElementById("cf-river").value.trim() || null,
    river_site_no: catchCtx ? catchCtx.river_site_no : null,
    lat: catchCtx ? catchCtx.lat : null,
    lon: catchCtx ? catchCtx.lon : null,
    length_in: lenRaw ? parseFloat(lenRaw) : null,
    fly_used: document.getElementById("cf-fly").value.trim() || null,
    notes: document.getElementById("cf-notes").value.trim() || null,
    occurred_at: whenRaw ? new Date(whenRaw).toISOString() : null,
  };
  const btn = document.getElementById("cf-save");
  btn.disabled = true; btn.textContent = "Saving…";
  try {
    const r = await fetch("/api/catches", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error("save failed");
    closeModal("catch-modal");
  } catch {
    err.textContent = "Could not save. Try again.";
  }
  btn.disabled = false; btn.textContent = "Save catch";
}

async function openMyCatches() {
  const panel = document.getElementById("catches-panel");
  panel.hidden = false;
  const list = document.getElementById("catches-list");
  list.innerHTML = '<div class="catches-empty">Loading…</div>';
  try {
    const data = await fetch("/api/catches").then((r) => r.json());
    document.getElementById("catches-count").textContent =
      data.total ? `${data.total} catch${data.total === 1 ? "" : "es"}` : "";
    renderCatchList(data.catches || []);
  } catch {
    list.innerHTML = '<div class="catches-empty">Could not load your catches.</div>';
  }
}

function renderCatchList(catches) {
  const list = document.getElementById("catches-list");
  if (!catches.length) {
    list.innerHTML =
      '<div class="catches-empty"><div class="catches-empty-ic">🎣</div>' +
      "<p>No catches logged yet.</p>" +
      "<p class=\"modal-fine\">Tap any river on the map and hit " +
      "“Log a catch” — we’ll capture the conditions automatically.</p></div>";
    return;
  }
  list.innerHTML = "";
  for (const c of catches) {
    const when = c.occurred_at ? new Date(c.occurred_at) : null;
    const dateStr = when
      ? when.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : "";
    const env = c.env || {};
    const envChips = [
      env.flow_cfs != null ? `💧${env.flow_cfs}cfs` : null,
      env.water_temp_f != null ? `🌡${env.water_temp_f}°F` : null,
      env.air_temp_f != null ? `☁${env.air_temp_f}°F` : null,
    ].filter(Boolean).map(esc).join("  ");
    const sub = [
      c.species, c.length_in != null ? `${c.length_in}"` : null, c.fly_used,
    ].filter(Boolean).map(esc).join(" · ");
    const row = document.createElement("div");
    row.className = "catch-row";
    row.innerHTML =
      `<div class="catch-row-head"><span class="catch-date">${esc(dateStr)}</span>` +
      `<span class="catch-river">${esc(c.river_name || "Unknown water")}</span></div>` +
      `<div class="catch-sub">${sub}</div>` +
      (envChips ? `<div class="catch-env">${envChips}</div>` : "") +
      (c.notes ? `<div class="catch-notes">${esc(c.notes)}</div>` : "") +
      `<button class="catch-del" data-id="${c.id}">Delete</button>`;
    row.querySelector(".catch-del").onclick = () => deleteCatch(c.id, row);
    list.appendChild(row);
  }
}

async function deleteCatch(id, rowEl) {
  if (!confirm("Delete this catch?")) return;
  try {
    const r = await fetch(`/api/catches/${id}`, { method: "DELETE" });
    if (r.ok || r.status === 204) {
      rowEl.remove();
      const list = document.getElementById("catches-list");
      if (!list.children.length) renderCatchList([]);
    }
  } catch {}
}

function wireCatchUI() {
  const form = document.getElementById("catch-form");
  if (form) form.addEventListener("submit", submitCatch);
  const whenInput = document.getElementById("cf-when");
  if (whenInput) whenInput.addEventListener("change", loadEnrichmentPreview);
  const back = document.getElementById("catches-back");
  if (back) back.addEventListener("click", () => {
    document.getElementById("catches-panel").hidden = true;
  });
}

if ("serviceWorker" in navigator) {
  // Auto-reload once when a new service worker takes control, so a deploy
  // propagates fresh JS/CSS without a manual cache clear. Only armed when
  // the page is already controlled (a returning visit) -- on the very
  // first visit there's no controller yet and no stale assets to replace,
  // so we skip the reload to avoid a pointless first-load refresh.
  if (navigator.serviceWorker.controller) {
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (refreshing) return;
      refreshing = true;
      window.location.reload();
    });
  }
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
