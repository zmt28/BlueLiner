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

// Keep popups inside small screens.
function popupOpts() {
  return { maxWidth: Math.min(420, (window.innerWidth || 420) - 32) };
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
const riversLayer = L.layerGroup().addTo(map);
const pinsLayer = L.layerGroup().addTo(map);

// Trout-stream lines are heavy; off by default (toggle in the control).
L.control.layers(null, {
  "Streams (USGS)": hydroLayer,
  "Trout Streams": troutLayer,
  "Saved Pins": pinsLayer,
}, { collapsed: true }).addTo(map);

let allRivers = [];

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

function renderRivers() {
  riversLayer.clearLayers();
  const cond = document.getElementById("cond-select").value;
  const troutOnly = document.getElementById("trout-only").checked;
  const stockedOnly = document.getElementById("stocked-only").checked;
  const hatch = document.getElementById("hatch-select").value;

  for (const r of allRivers) {
    if (troutOnly && !r.on_trout) continue;
    if (stockedOnly && !r.near_stocked) continue;
    if (cond !== "any" && r.conditions.overall !== cond) continue;
    const ah = r.active_hatches || [];
    if (hatch === "active" && !ah.length) continue;
    if (hatch !== "any" && hatch !== "active" && !ah.includes(hatch)) continue;

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

async function loadRivers(state) {
  const data = await fetch(`/api/rivers?state=${state}`).then((r) => r.json());
  allRivers = (data && data.rivers) || [];
  populateHatchOptions();
  renderRivers();
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

document.getElementById("cond-select").onchange = renderRivers;
document.getElementById("trout-only").onchange = renderRivers;
document.getElementById("stocked-only").onchange = renderRivers;
document.getElementById("hatch-select").onchange = renderRivers;

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
}
init();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
