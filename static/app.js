"use strict";

// code -> { name, center }, loaded from /api/states (states.py is the
// single source of truth). Populated during init().
let STATES = {};
let currentSt = "MD";
const STATE_ZOOM = 7;

function currentState() {
  const p = new URLSearchParams(location.search).get("state") || "MD";
  const s = p.toUpperCase();
  return STATES[s] ? s : (STATES.MD ? "MD" : Object.keys(STATES)[0]);
}

// Opaque per-device token (no login). Persists in localStorage; sent on
// every pins request so saved pins are scoped to this device/browser.
function deviceToken() {
  let t = localStorage.getItem("bl_device");
  if (!t) {
    t = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : String(Date.now()) + Math.random().toString(36).slice(2);
    localStorage.setItem("bl_device", t);
  }
  return t;
}
const DEVICE_HEADER = { "X-Device-Token": deviceToken() };

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// Keep popups inside the screen; maxHeight makes Leaflet add an internal
// scroll container so tall river popups (hatch + several gauges) scroll.
function popupOpts() {
  return {
    maxWidth: Math.min(420, (window.innerWidth || 420) - 32),
    maxHeight: Math.round((window.innerHeight || 700) * 0.7),
    autoPan: true,
  };
}

const map = L.map("map");
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  subdomains: "abcd",
  maxZoom: 19,
}).addTo(map);

// Labeled rivers/streams: free national USGS "Hydro Cached" overlay (no key,
// no deps). Transparent raster designed to sit on a basemap. ArcGIS cached
// tiles are /tile/{level}/{row}/{col} == {z}/{y}/{x}.
const hydroLayer = L.tileLayer(
  "https://basemap.nationalmap.gov/arcgis/rest/services/USGSHydroCached/MapServer/tile/{z}/{y}/{x}",
  {
    opacity: 0.85,
    maxZoom: 19,
    attribution: "Hydrography &copy; USGS The National Map",
  }
).addTo(map);

const troutLayer = L.geoJSON(null, {
  style: { color: "#1abc9c", weight: 2.5, opacity: 0.7 },
  onEachFeature: (f, l) => {
    const p = f.properties || {};
    const n = p.NAME || p.GNIS_Name || p.STream_Nam;
    if (n) l.bindTooltip(String(n), { sticky: true });
  },
});
const riverLinesLayer = L.layerGroup().addTo(map);
const riversLayer = L.layerGroup().addTo(map);
const pinsLayer = L.layerGroup().addTo(map);

// Trout-stream lines are heavy; off by default (toggle in the control).
L.control.layers(null, {
  "Streams (USGS)": hydroLayer,
  "Trout Streams": troutLayer,
  "Saved Pins": pinsLayer,
}, { collapsed: true }).addTo(map);

let allRivers = [];
// One representation per river: the NLDI flowline when we have it, else
// a pin. riverLineBySite holds loaded line layers; riverGeomLoaded marks
// site_nos already attempted (so we never refetch / never retry empties).
const riverLineBySite = new Map();
const riverGeomLoaded = new Set();    // site_nos with a final result (or empty)
const riverGeomInFlight = new Set();  // site_nos being fetched right now

// -- 1-yr USGS trend sparkline (dependency-free SVG) --

function sparkline(series) {
  if (!series || !series.length) {
    return '<div class="bl-trend-msg">No 1-yr data for this site.</div>';
  }
  const s = series.find((x) => x.parameter === "00060") || series[0];
  const pts = s.points || [];
  if (pts.length < 2) {
    return '<div class="bl-trend-msg">Not enough data to chart.</div>';
  }
  const vals = pts.map((p) => p.value);
  const min = Math.min(...vals), max = Math.max(...vals);
  const W = 300, H = 80, PX = 4, PY = 6, n = pts.length;
  const X = (i) => PX + (i * (W - 2 * PX)) / (n - 1);
  const Y = (v) => (max === min ? H / 2
    : (H - PY) - ((v - min) * (H - 2 * PY)) / (max - min));
  let d = "";
  pts.forEach((p, i) => {
    d += (i ? "L" : "M") + X(i).toFixed(1) + " " + Y(p.value).toFixed(1) + " ";
  });
  const last = pts[pts.length - 1];
  return (
    `<div class="bl-trend-msg">${esc(s.name || s.parameter)} ` +
    `(${esc(s.unit || "")}) &mdash; last 12 months</div>` +
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" ` +
    `xmlns="http://www.w3.org/2000/svg">` +
    `<path d="${d}" fill="none" stroke="#2c6fbf" stroke-width="1.5"/></svg>` +
    `<div class="bl-trend-msg">min ${min.toFixed(0)} &middot; ` +
    `max ${max.toFixed(0)} &middot; now ${last.value.toFixed(0)} ` +
    `(${esc((last.date || "").slice(0, 10))})</div>`
  );
}

function wireTrend(e) {
  const root = e.popup.getElement();
  if (!root) return;
  // A river popup has one trend button per gauge.
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
        if (box) box.innerHTML = sparkline(d.series);
      } catch (_) {
        if (box) box.innerHTML = '<div class="bl-trend-msg">Trend unavailable.</div>';
      }
      btn.style.display = "none";
      e.popup.update();
    };
  });
}

// -- Gauges --

function populateHatchOptions() {
  const sel = document.getElementById("hatch-select");
  const cur = sel.value;
  const set = new Set();
  allRivers.forEach((r) => (r.active_hatches || []).forEach((h) => set.add(h)));
  const insects = [...set].sort();
  sel.innerHTML =
    '<option value="any">Any hatch</option>' +
    '<option value="active">Active hatch now</option>' +
    insects.map((i) => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "any";
}

function riverPasses(r) {
  const cond = document.getElementById("cond-select").value;
  const troutOnly = document.getElementById("trout-only").checked;
  const stockedOnly = document.getElementById("stocked-only").checked;
  const hatch = document.getElementById("hatch-select").value;
  if (troutOnly && !r.on_trout) return false;
  if (stockedOnly && !r.near_stocked) return false;
  if (cond !== "any" && r.conditions.overall !== cond) return false;
  const ah = r.active_hatches || [];
  if (hatch === "active" && !ah.length) return false;
  if (hatch !== "any" && hatch !== "active" && !ah.includes(hatch)) return false;
  return true;
}

// Exactly one clickable representation per river: the flowline if loaded,
// otherwise a pin (fallback for low zoom / not-yet-loaded / NLDI has no
// geometry, e.g. Bennett Creek). Centralized so the invariant holds for
// every state, filter, and zoom.
function renderRivers() {
  riversLayer.clearLayers();
  for (const r of allRivers) {
    const line = r.site_no ? riverLineBySite.get(r.site_no) : null;
    const pass = riverPasses(r);
    if (line) {
      if (pass && !riverLinesLayer.hasLayer(line)) riverLinesLayer.addLayer(line);
      if (!pass && riverLinesLayer.hasLayer(line)) riverLinesLayer.removeLayer(line);
      continue;  // line represents this river -- no redundant pin
    }
    if (!pass) continue;
    const m = L.circleMarker([r.lat, r.lon], {
      radius: 8, color: r.color, weight: 2,
      fill: true, fillColor: r.color, fillOpacity: 0.85,
    });
    const tBadge = r.on_trout
      ? ' <span style="color:#1abc9c;font-size:11px">&#x1f41f; Trout</span>'
      : "";
    const sBadge = r.near_stocked
      ? ' <span style="color:#e67e22;font-size:11px">Stocked</span>'
      : "";
    m.bindTooltip(
      `<b>${esc(r.name)}</b>${tBadge}${sBadge}` +
      `<br><span style="color:${r.color}">${esc(r.label)}</span>`
    );
    m.bindPopup(r.popup_html, popupOpts());
    m.on("popupopen", wireTrend);
    riversLayer.addLayer(m);
  }
}

// Trout streams cover the whole state now (large). Load lazily -- only
// when the user toggles the layer on -- and once per state, so the
// initial map (layer off by default) is never blocked by a multi-MB
// GeoJSON parse.
let troutLoadedState = null;
let troutLoading = false;

async function ensureTrout(state) {
  if (troutLoadedState === state || troutLoading) return;
  troutLoading = true;
  try {
    const t = await fetch(`/api/trout?state=${state}`).then((r) => r.json());
    troutLayer.clearLayers();
    troutLayer.addData(t);
    troutLoadedState = state;
  } catch (_) {
    /* leave layer empty; user can re-toggle to retry */
  } finally {
    troutLoading = false;
  }
}

map.on("overlayadd", (e) => {
  if (e.layer === troutLayer) ensureTrout(currentSt);
});

// -- Clickable river flowlines (USGS NLDI): lazy, viewport-bounded --
// Loading every river's geometry at once is the trout-layer trap, so we
// only fetch lines for rivers in the current view, when zoomed in,
// debounced, concurrency-capped, and cached per site for the session.
const RIVER_LINE_MIN_ZOOM = 9;
const RIVER_LINE_MAX_PER_PASS = 30;     // batch size; we loop until done
const RIVER_LINE_CONCURRENCY = 8;
const RIVER_LINE_MAX_TOTAL = 400;       // safety ceiling per invocation
let riverLinePass = 0;

async function fetchRiverLine(r) {
  try {
    const fc = await fetch(
      `/api/river_geom?site_no=${encodeURIComponent(r.site_no)}`
    ).then((res) => res.json());
    // Empty geometry is a final answer (NLDI has no flowline) -> pin
    // fallback; mark loaded so we don't refetch it.
    if (fc && fc.features && fc.features.length) {
      const line = L.geoJSON(fc, {
        style: { color: r.color, weight: 5, opacity: 0.6 },
      });
      line.bindPopup(r.popup_html, popupOpts());
      line.on("popupopen", wireTrend);
      riverLineBySite.set(r.site_no, line);   // renderRivers() places it
    }
    riverGeomLoaded.add(r.site_no);
  } catch (_) {
    // Transient failure: leave it unloaded so a later pass retries it.
  } finally {
    riverGeomInFlight.delete(r.site_no);
  }
}

async function loadVisibleRiverLines() {
  if (map.getZoom() < RIVER_LINE_MIN_ZOOM) return;
  const pass = ++riverLinePass;
  const c = map.getCenter();
  let fetched = 0;

  while (fetched < RIVER_LINE_MAX_TOTAL && pass === riverLinePass) {
    if (map.getZoom() < RIVER_LINE_MIN_ZOOM) return;
    const b = map.getBounds();
    const todo = [];
    for (const r of allRivers) {
      if (!r.site_no) continue;
      if (riverGeomLoaded.has(r.site_no) || riverGeomInFlight.has(r.site_no)) continue;
      if (!riverPasses(r)) continue;        // don't fetch filtered-out rivers
      if (!b.contains([r.lat, r.lon])) continue;
      todo.push(r);
    }
    if (!todo.length) break;
    // Center-out: the rivers the user is looking at fill in first.
    todo.sort(
      (a, z) =>
        (a.lat - c.lat) ** 2 + (a.lon - c.lng) ** 2 -
        ((z.lat - c.lat) ** 2 + (z.lon - c.lng) ** 2)
    );
    const batch = todo.slice(0, RIVER_LINE_MAX_PER_PASS);
    let i = 0;
    const worker = async () => {
      while (i < batch.length && pass === riverLinePass) {
        // Mark in-flight only for the one we're about to fetch, so a
        // superseded pass can't strand markers (fetchRiverLine clears
        // them in its finally).
        const r = batch[i++];
        riverGeomInFlight.add(r.site_no);
        await fetchRiverLine(r);
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(RIVER_LINE_CONCURRENCY, batch.length) }, worker)
    );
    fetched += batch.length;
    if (pass === riverLinePass) renderRivers();  // lines progressively replace pins
  }
}

// -- Hybrid loading: state overview when zoomed out, live viewport when in --
const VIEWPORT_MIN_ZOOM = 9;
let stateRivers = [];               // last per-state set (zoomed-out overview)
let viewportMode = false;
const _viewportCache = new Map();   // rounded "w,s,e,n" -> rivers
let _viewportSeq = 0;

async function loadViewportRivers() {
  const b = map.getBounds();
  const round = (x) => x.toFixed(2);
  const key = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map(round).join(",");
  const seq = ++_viewportSeq;
  let rivers = _viewportCache.get(key);
  if (!rivers) {
    try {
      const q = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
      const data = await fetch(
        `/api/rivers?bbox=${encodeURIComponent(q)}`
      ).then((r) => r.json());
      rivers = (data && data.rivers) || [];
      _viewportCache.set(key, rivers);
      if (_viewportCache.size > 30) {
        _viewportCache.delete(_viewportCache.keys().next().value);
      }
    } catch (_) { return; }   // keep current view; transient failure
  }
  if (seq !== _viewportSeq) return;       // a newer pan/zoom superseded us
  viewportMode = true;
  allRivers = rivers;
  populateHatchOptions();
  renderRivers();
  const q = `${b.getWest()},${b.getSouth()},${b.getEast()},${b.getNorth()}`;
  startRiverLines(`bbox=${encodeURIComponent(q)}`);  // batched + re-poll
  loadVisibleRiverLines();                           // per-site fallback (zoomed-in only)
}

function refreshForView() {
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();
  } else if (viewportMode) {
    viewportMode = false;                 // zoomed back out -> state overview
    allRivers = stateRivers;
    populateHatchOptions();
    renderRivers();
  }
}

let _viewTimer = null;
map.on("moveend", () => {
  clearTimeout(_viewTimer);
  _viewTimer = setTimeout(refreshForView, 400);
});

// Draw EVERY river as its precomputed flowline in one shot, at any zoom.
// /api/river_lines is a single gzipped Postgres read (no per-river NLDI
// fan-out), so lines appear immediately instead of trickling in.
async function loadRiverLines(qs) {
  let fc;
  try {
    fc = await fetch(`/api/river_lines?${qs}`).then((r) => r.json());
  } catch (_) { return; }                 // keep pins; transient failure
  if (!fc || !fc.features || !fc.features.length) return;
  const bySite = new Map();
  for (const f of fc.features) {
    const p = f.properties || {};
    if (!p.site_no) continue;
    let g = bySite.get(p.site_no);
    if (!g) { g = { type: "FeatureCollection", features: [], color: p.color }; bySite.set(p.site_no, g); }
    g.features.push(f);
  }
  const riverBySite = new Map();
  for (const r of allRivers) if (r.site_no) riverBySite.set(r.site_no, r);
  for (const [sn, g] of bySite) {
    if (riverLineBySite.has(sn)) continue;
    const r = riverBySite.get(sn);
    const color = (r && r.color) || g.color || "#2c6fbf";
    const line = L.geoJSON(g, { style: { color, weight: 5, opacity: 0.6 } });
    if (r) { line.bindPopup(r.popup_html, popupOpts()); line.on("popupopen", wireTrend); }
    riverLineBySite.set(sn, line);
    riverGeomLoaded.add(sn);              // per-site fallback now skips it
  }
  renderRivers();                         // lines replace pins
}

// Geometry is backfilled into Postgres asynchronously, so on a cold
// state the first /api/river_lines may be partial/empty. Re-poll with
// backoff, merging newly-ready lines, until every river has one (or we
// give up -- some gauges genuinely have no NLDI flowline and stay pins).
// A token cancels the loop the moment the state/viewport changes.
let _linesToken = 0;
async function startRiverLines(qs) {
  const token = ++_linesToken;
  const delays = [0, 6000, 10000, 16000, 24000, 35000, 50000];
  for (let i = 0; i < delays.length; i++) {
    if (token !== _linesToken) return;          // superseded by a newer view
    if (delays[i]) {
      await new Promise((r) => setTimeout(r, delays[i]));
      if (token !== _linesToken) return;
    }
    await loadRiverLines(qs);
    if (token !== _linesToken) return;
    const missing = allRivers.some(
      (r) => r.site_no && !riverLineBySite.has(r.site_no)
    );
    if (!missing) return;                       // fully covered -> done
  }
}

// A lazy (never-visited) state returns [] while the background precompute
// runs; refetch once so it fills in without the user reloading.
let _lazyRetry = null;
function scheduleLazyRetry(state) {
  clearTimeout(_lazyRetry);
  _lazyRetry = setTimeout(() => {
    if (currentSt === state && !viewportMode) loadRivers(state);
  }, 20000);
}

async function loadRivers(state) {
  const data = await fetch(`/api/rivers?state=${state}`).then((r) => r.json());
  stateRivers = (data && data.rivers) || [];
  riverLinesLayer.clearLayers();
  riverLineBySite.clear();
  riverGeomLoaded.clear();
  riverGeomInFlight.clear();
  _viewportCache.clear();
  if (map.getZoom() >= VIEWPORT_MIN_ZOOM) {
    loadViewportRivers();                 // already zoomed in: viewport drives
  } else {
    viewportMode = false;
    allRivers = stateRivers;
    populateHatchOptions();
    renderRivers();
    if (stateRivers.length) {
      startRiverLines(`state=${encodeURIComponent(state)}`);
    } else {
      scheduleLazyRetry(state);           // not computed yet -> auto-fill
    }
  }
}

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

// -- Filters / state switching (no full reload) --

function onFilterChange() {
  renderRivers();                 // re-apply filter to pins + lines
  loadVisibleRiverLines();        // fetch lines for newly-passing in-view rivers
}
document.getElementById("cond-select").onchange = onFilterChange;
document.getElementById("trout-only").onchange = onFilterChange;
document.getElementById("stocked-only").onchange = onFilterChange;
document.getElementById("hatch-select").onchange = onFilterChange;

document.getElementById("state-select").onchange = (e) => {
  const s = e.target.value;
  currentSt = s;
  history.replaceState(null, "", `/map?state=${s.toLowerCase()}`);
  map.setView(STATES[s].center, STATE_ZOOM);
  loadRivers(s);
  // Refresh trout for the new state only if the layer is currently shown.
  troutLoadedState = null;
  if (map.hasLayer(troutLayer)) ensureTrout(s);
};

// -- Mobile: filter sheet + collapsible legend --

const controls = document.getElementById("controls");
const filtersToggle = document.getElementById("filters-toggle");
function setSheet(open) {
  controls.classList.toggle("open", open);
  filtersToggle.classList.toggle("active", open);
  filtersToggle.setAttribute("aria-expanded", open ? "true" : "false");
}
filtersToggle.onclick = () => setSheet(!controls.classList.contains("open"));
document.getElementById("controls-done").onclick = () => setSheet(false);

const legend = document.getElementById("legend");
const legendToggle = document.getElementById("legend-toggle");
legendToggle.onclick = () => {
  const collapsed = legend.classList.toggle("collapsed");
  legendToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
};

// -- Init --

async function init() {
  const list = await fetch("/api/states").then((r) => r.json());
  const sel = document.getElementById("state-select");
  sel.innerHTML = "";
  for (const s of list) {
    STATES[s.code] = { name: s.name, center: s.center };
    const opt = document.createElement("option");
    opt.value = s.code;
    opt.textContent = s.name;
    sel.appendChild(opt);
  }
  const state = currentState();
  currentSt = state;
  sel.value = state;
  map.setView(STATES[state].center, STATE_ZOOM);
  loadRivers(state);
  loadPins();
  await initAuth();
}
init();

// -- Accounts (Phase 1) ---------------------------------------------

// Auth state derived from /api/me on load. null = signed out.
let CURRENT_USER = null;

async function initAuth() {
  await loadAuthState();
  wireAuthHandlers();
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

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
