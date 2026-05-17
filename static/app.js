"use strict";

// Center/zoom per state (mirrors states.py STATES["center"] and US_CENTER).
const STATES = {
  MD: { center: [38.9784, -76.4922], zoom: 8 },
  VA: { center: [37.4316, -78.6569], zoom: 8 },
  WV: { center: [38.5976, -80.4549], zoom: 8 },
  ALL: { center: [39.8283, -98.5795], zoom: 6 },
};

function currentState() {
  const p = new URLSearchParams(location.search).get("state") || "MD";
  const s = p.toUpperCase();
  return STATES[s] ? s : "MD";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const map = L.map("map");
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  subdomains: "abcd",
  maxZoom: 19,
}).addTo(map);

const waterwaysLayer = L.geoJSON(null, {
  style: { color: "#4a90d9", weight: 1.2, opacity: 0.4 },
  onEachFeature: (f, l) => {
    const n = f.properties && f.properties.FULLNAME;
    if (n) l.bindTooltip(String(n), { sticky: true });
  },
});
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
const pinsLayer = L.layerGroup();

[waterwaysLayer, troutLayer, troutGaugesLayer, otherGaugesLayer, pinsLayer]
  .forEach((g) => g.addTo(map));

L.control.layers(null, {
  "Waterways": waterwaysLayer,
  "Trout Streams": troutLayer,
  "Trout Stream Gauges": troutGaugesLayer,
  "All Other Gauges": otherGaugesLayer,
  "Saved Pins": pinsLayer,
}, { collapsed: true }).addTo(map);

let allGauges = [];

function renderGauges() {
  troutGaugesLayer.clearLayers();
  otherGaugesLayer.clearLayers();
  const cond = document.getElementById("cond-select").value;
  const troutOnly = document.getElementById("trout-only").checked;

  for (const g of allGauges) {
    if (troutOnly && !g.on_trout) continue;
    if (cond !== "any" && g.conditions.overall !== cond) continue;

    const m = L.circleMarker([g.lat, g.lon], {
      radius: 7, color: g.color, weight: 2,
      fill: true, fillColor: g.color, fillOpacity: 0.8,
    });
    const badge = g.on_trout
      ? ' <span style="color:#1abc9c;font-size:11px">&#x1f41f; Trout Water</span>'
      : "";
    m.bindTooltip(
      `<b>${esc(g.name)}</b>${badge}<br><span style="color:${g.color}">${esc(g.label)}</span>`
    );
    m.bindPopup(g.popup_html, { maxWidth: 420 });
    (g.on_trout ? troutGaugesLayer : otherGaugesLayer).addLayer(m);
  }
}

async function loadGeo(state) {
  const [w, t] = await Promise.all([
    fetch(`/api/waterways?state=${state}`).then((r) => r.json()),
    fetch(`/api/trout?state=${state}`).then((r) => r.json()),
  ]);
  waterwaysLayer.clearLayers();
  waterwaysLayer.addData(w);
  troutLayer.clearLayers();
  troutLayer.addData(t);
}

async function loadGauges(state) {
  const data = await fetch(`/api/gauges?state=${state}`).then((r) => r.json());
  allGauges = (data && data.gauges) || [];
  renderGauges();
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
    `<button class="pin-del" type="button">Delete</button></div>`
  );
  m.on("popupopen", (e) => {
    const btn = e.popup.getElement().querySelector(".pin-del");
    if (btn) {
      btn.onclick = async () => {
        await fetch(`/api/pins/${p.id}`, { method: "DELETE" });
        pinsLayer.removeLayer(m);
        map.closePopup();
      };
    }
  });
  pinsLayer.addLayer(m);
}

async function loadPins() {
  const pins = await fetch("/api/pins").then((r) => r.json());
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
    headers: { "Content-Type": "application/json" },
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

document.getElementById("state-select").onchange = (e) => {
  const s = e.target.value;
  history.replaceState(null, "", `/map?state=${s.toLowerCase()}`);
  const meta = STATES[s];
  map.setView(meta.center, meta.zoom);
  loadGeo(s);
  loadGauges(s);
};

// -- Init --

const state = currentState();
document.getElementById("state-select").value = state;
map.setView(STATES[state].center, STATES[state].zoom);
loadGeo(state);
loadGauges(state);
loadPins();
