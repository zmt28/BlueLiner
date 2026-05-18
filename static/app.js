"use strict";

// Center/zoom per state (mirrors states.py STATES["center"] and US_CENTER).
const STATES = {
  MD: { center: [38.9784, -76.4922], zoom: 8 },
  VA: { center: [37.4316, -78.6569], zoom: 8 },
  WV: { center: [38.5976, -80.4549], zoom: 8 },
  ALL: { center: [39.8283, -98.5795], zoom: 6 },
};
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function currentState() {
  const p = new URLSearchParams(location.search).get("state") || "MD";
  const s = p.toUpperCase();
  return STATES[s] ? s : "MD";
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

function monthsLabel(mm) {
  if (!mm || mm.length !== 2) return "Year-round";
  const [s, e] = mm;
  if (s === 1 && e === 12) return "Year-round";
  return `${MON[s - 1]}-${MON[e - 1]}`;
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
const troutGaugesLayer = L.layerGroup();
const otherGaugesLayer = L.layerGroup();
const stockingLayer = L.layerGroup();
const pinsLayer = L.layerGroup();

[troutLayer, troutGaugesLayer, otherGaugesLayer,
 stockingLayer, pinsLayer].forEach((g) => g.addTo(map));

L.control.layers(null, {
  "Streams (USGS)": hydroLayer,
  "Trout Streams": troutLayer,
  "Trout Stream Gauges": troutGaugesLayer,
  "All Other Gauges": otherGaugesLayer,
  "Recently Stocked": stockingLayer,
  "Saved Pins": pinsLayer,
}, { collapsed: true }).addTo(map);

let allGauges = [];

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
  const btn = root.querySelector(".bl-trend-btn");
  const box = root.querySelector(".bl-trend");
  if (!btn || !box || btn.dataset.wired) return;
  btn.dataset.wired = "1";
  btn.onclick = async () => {
    const site = btn.getAttribute("data-site");
    btn.disabled = true;
    box.innerHTML = '<div class="bl-trend-msg">Loading 1-yr trend&hellip;</div>';
    try {
      const d = await fetch(
        `/api/history?site_no=${encodeURIComponent(site)}`
      ).then((r) => r.json());
      box.innerHTML = sparkline(d.series);
    } catch (_) {
      box.innerHTML = '<div class="bl-trend-msg">Trend unavailable.</div>';
    }
    btn.style.display = "none";
    e.popup.update();
  };
}

// -- Gauges --

function populateHatchOptions() {
  const sel = document.getElementById("hatch-select");
  const cur = sel.value;
  const set = new Set();
  allGauges.forEach((g) => (g.active_hatches || []).forEach((h) => set.add(h)));
  const insects = [...set].sort();
  sel.innerHTML =
    '<option value="any">Any hatch</option>' +
    '<option value="active">Active hatch now</option>' +
    insects.map((i) => `<option value="${esc(i)}">${esc(i)}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "any";
}

function renderGauges() {
  troutGaugesLayer.clearLayers();
  otherGaugesLayer.clearLayers();
  const cond = document.getElementById("cond-select").value;
  const troutOnly = document.getElementById("trout-only").checked;
  const stockedOnly = document.getElementById("stocked-only").checked;
  const hatch = document.getElementById("hatch-select").value;

  for (const g of allGauges) {
    if (troutOnly && !g.on_trout) continue;
    if (stockedOnly && !g.near_stocked) continue;
    if (cond !== "any" && g.conditions.overall !== cond) continue;
    const ah = g.active_hatches || [];
    if (hatch === "active" && !ah.length) continue;
    if (hatch !== "any" && hatch !== "active" && !ah.includes(hatch)) continue;

    const m = L.circleMarker([g.lat, g.lon], {
      radius: 7, color: g.color, weight: 2,
      fill: true, fillColor: g.color, fillOpacity: 0.8,
    });
    const tBadge = g.on_trout
      ? ' <span style="color:#1abc9c;font-size:11px">&#x1f41f; Trout Water</span>'
      : "";
    const sBadge = g.near_stocked
      ? ' <span style="color:#e67e22;font-size:11px">Stocked</span>'
      : "";
    m.bindTooltip(
      `<b>${esc(g.name)}</b>${tBadge}${sBadge}` +
      `<br><span style="color:${g.color}">${esc(g.label)}</span>`
    );
    m.bindPopup(g.popup_html, popupOpts());
    m.on("popupopen", wireTrend);
    (g.on_trout ? troutGaugesLayer : otherGaugesLayer).addLayer(m);
  }
}

async function loadGeo(state) {
  const t = await fetch(`/api/trout?state=${state}`).then((r) => r.json());
  troutLayer.clearLayers();
  troutLayer.addData(t);
}

async function loadGauges(state) {
  const data = await fetch(`/api/gauges?state=${state}`).then((r) => r.json());
  allGauges = (data && data.gauges) || [];
  populateHatchOptions();
  renderGauges();
}

// -- Recently stocked --

function addStockMarker(f) {
  const c = f.geometry && f.geometry.coordinates;
  if (!c) return;
  const p = f.properties || {};
  const icon = L.divIcon({
    className: "bl-stock",
    html: '<div class="bl-stock-dot"></div>',
    iconSize: [13, 13], iconAnchor: [7, 7],
  });
  const sp = (p.species || []).join(", ");
  const m = L.marker([c[1], c[0]], { icon });
  m.bindPopup(
    `<div class="stock-popup"><div class="sp-title">${esc(p.water)}</div>` +
    (p.category ? `<div class="sp-row">${esc(p.category)}</div>` : "") +
    (sp ? `<div class="sp-row">Species: ${esc(sp)}</div>` : "") +
    `<div class="sp-row">Season: ${esc(monthsLabel(p.season_months))}</div>` +
    (p.agency_url
      ? `<div class="sp-row"><a href="${esc(p.agency_url)}" target="_blank" ` +
        `rel="noopener">Stocking schedule &#x2197;</a></div>`
      : "") +
    `</div>`,
    popupOpts()
  );
  stockingLayer.addLayer(m);
}

async function loadStocking(state) {
  const fc = await fetch(`/api/stocking?state=${state}`).then((r) => r.json());
  stockingLayer.clearLayers();
  (fc.features || []).forEach(addStockMarker);
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

document.getElementById("cond-select").onchange = renderGauges;
document.getElementById("trout-only").onchange = renderGauges;
document.getElementById("stocked-only").onchange = renderGauges;
document.getElementById("hatch-select").onchange = renderGauges;

document.getElementById("state-select").onchange = (e) => {
  const s = e.target.value;
  history.replaceState(null, "", `/map?state=${s.toLowerCase()}`);
  const meta = STATES[s];
  map.setView(meta.center, meta.zoom);
  loadGeo(s);
  loadGauges(s);
  loadStocking(s);
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

const state = currentState();
document.getElementById("state-select").value = state;
map.setView(STATES[state].center, STATES[state].zoom);
loadGeo(state);
loadGauges(state);
loadStocking(state);
loadPins();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  });
}
