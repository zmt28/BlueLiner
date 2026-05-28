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

// code -> { name, center }, loaded from /api/states (states.py is the
// single source of truth). Populated during init().
//
// Note: STATES + currentSt stay in this legacy file for PR B1b --
// they're hot mutable state that init() and the state selector
// handler update throughout. They're mirrored to window so the
// helpers extracted into static/src/state.ts (deviceToken,
// DEVICE_HEADER, currentState, STATE_ZOOM) can read them. A
// follow-up PR fully extracts the state-selector code path and
// migrates these into state.ts proper.
let STATES = {};
let currentSt = "MD";
window.STATES = STATES;          // bridge: state.ts's currentState() reads this
const STATE_ZOOM = window.STATE_ZOOM;       // re-exposed from state.ts
const DEVICE_HEADER = window.DEVICE_HEADER; // re-exposed from state.ts
const deviceToken = window.deviceToken;     // re-exposed from state.ts
const currentState = window.currentState;   // re-exposed from state.ts
const esc = window.esc;                     // re-exposed from util.ts
const popupOpts = window.popupOpts;         // re-exposed from util.ts

// -- Map + base layers -- extracted to static/src/map-setup.ts in PR
// B1d. The Leaflet map instance, base-map provider catalog, base-map
// switching + bl_basemap localStorage, and USGS Hydro overlay all
// live there now. Re-exposed via window so the layer-toggle wiring
// and base-map segmented control further down can resolve unchanged.
// `currentBaseKey` is read once at controls init (segment highlight)
// and then segment buttons manage their own .on classes -- a stale
// const rebind here is fine.
const map = window.map;
const setBaseMap = window.setBaseMap;
const currentBaseKey = window.currentBaseKey;
const hydroLayer = window.hydroLayer;

const troutLayer = L.geoJSON(null, {
  style: { color: "#1abc9c", weight: 2.5, opacity: 0.7 },
  onEachFeature: (f, l) => {
    const p = f.properties || {};
    const n = p.NAME || p.GNIS_Name || p.STream_Nam;
    if (n) l.bindTooltip(String(n), { sticky: true });
  },
});

// Access points: type-coded markers, lazy-loaded per state via
// /api/access?state=. Different glyph + tone per access type so a
// boat ramp is visually distinct from a walk-in trail at a glance.
const ACCESS_TYPE_META = {
  boat_ramp:      { glyph: "B", color: "#d97706" },
  walk_in:        { glyph: "W", color: "#0891b2" },
  wading_access:  { glyph: "W", color: "#0891b2" },
  pier:           { glyph: "P", color: "#7c3aed" },
  parking:        { glyph: "P", color: "#475569" },
};
const accessLayer = L.layerGroup();

// Public-lands parcels (PAD-US). Vector polygons keyed off the
// `public_access` tier rather than the manager type -- the angler's
// primary question is "can I walk in here?", not "is this BLM or
// USFS?" Two visual tiers: green for OA (Open Access) and dashed
// yellow for RA (Restricted -- permit, walk-in, seasonal). UK/XA
// features are filtered out at build time, not rendered. Loaded
// per-viewport via /api/public_lands?bbox= -- same lazy bbox-bound
// pattern as clickable streams.
const PUBLIC_LANDS_STYLE = {
  OA: { fillColor: "#2d6a4f", color: "#1b4332", fillOpacity: 0.28,
        weight: 0.8, dashArray: null },
  RA: { fillColor: "#eab308", color: "#854d0e", fillOpacity: 0.22,
        weight: 1.0, dashArray: "4,4" },
};
const PUBLIC_LANDS_DEFAULT_STYLE = PUBLIC_LANDS_STYLE.OA;
function publicLandsStyle(feature) {
  const tier = (feature && feature.properties && feature.properties.public_access) || "OA";
  return PUBLIC_LANDS_STYLE[tier] || PUBLIC_LANDS_DEFAULT_STYLE;
}
// Access-tier chip labels for the popup. PAD-US codes are terse;
// expand to legible strings + map to chip CSS variants.
const PA_ACCESS_LABEL = {
  OA: "Open access",
  RA: "Restricted access",
  XA: "Closed",
  UK: "Unknown",
};
function publicLandsPopupHtml(p) {
  const tierCode = p.public_access || "UK";
  const tierLabel = PA_ACCESS_LABEL[tierCode] || PA_ACCESS_LABEL.UK;
  const tierChip =
    `<span class="ap-chip pa-chip-${esc(tierCode)}">${esc(tierLabel)}</span>`;
  const lines = [
    `<div class="ap-name">${esc(p.unit_name || "Public land parcel")}</div>`,
  ];
  const sub = [];
  if (p.manager_name) sub.push(esc(p.manager_name));
  if (p.designation) sub.push(esc(p.designation));
  if (sub.length) lines.push(`<div class="ap-meta">${sub.join(" &middot; ")}</div>`);
  lines.push(`<div class="ap-meta" style="margin-top:6px">${tierChip}</div>`);
  if (p.state_nm) {
    lines.push(`<div class="ap-notes">${esc(p.state_nm)}</div>`);
  }
  return `<div class="ap-popup">${lines.join("")}</div>`;
}
const publicLandsLayer = L.geoJSON(null, {
  style: publicLandsStyle,
  onEachFeature: (f, layer) => {
    layer.bindPopup(publicLandsPopupHtml(f.properties || {}), popupOpts());
  },
});

const riverLinesLayer = L.layerGroup().addTo(map);
const riversLayer = L.layerGroup().addTo(map);
const pinsLayer = L.layerGroup().addTo(map);

// Two stacked layers per feature: a thin styled visible line and a
// transparent fat "hit casing" on top to catch finger taps on mobile
// (a 4px line is still a poor touch target). Visible is non-interactive
// so clicks unambiguously go to the casing; both render the same FC.
const clickableVisible = L.geoJSON(null, {
  style: (f) => streamStyle(f.properties),
  interactive: false,
});
const clickableHit = L.geoJSON(null, {
  style: () => ({ color: "#000", weight: 16, opacity: 0, lineCap: "round" }),
  onEachFeature: (f, l) => l.on("click", (e) => {
    L.DomEvent.stop(e);
    onStreamClick(f.properties, e.latlng);
  }),
});
const clickableLayer = L.featureGroup(
  [clickableVisible, clickableHit]).addTo(map);

// Layer visibility used to live in Leaflet's default top-right control;
// it now sits inside the Layers tab of the unified #controls-panel.
// Wiring happens at init time so the tab's checkboxes drive add/remove
// on the map.

let allRivers = [];
// One representation per river: the NLDI flowline when we have it, else
// a pin. riverLineBySite holds loaded line layers; riverGeomLoaded marks
// site_nos already attempted (so we never refetch / never retry empties).
const riverLineBySite = new Map();
const riverGeomLoaded = new Set();    // site_nos with a final result (or empty)
const riverGeomInFlight = new Set();  // site_nos being fetched right now

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

// -- River detail panel (drawer / bottom sheet) ----------------------

let _panelHideTimer = null;
let _selectedRiver = null;        // { layer, base } for highlight restore
let _lastPanelOpenTs = 0;

// refreshIcons() extracted to static/src/util.ts in PR B1c.
const refreshIcons = window.refreshIcons;

function openRiverPanel(river, layer, baseStyle) {
  const panel = document.getElementById("river-panel");
  const body = document.getElementById("river-panel-body");
  if (!panel || !body) return;
  clearTimeout(_panelHideTimer);
  body.innerHTML = river.popup_html || "";
  body.scrollTop = 0;
  panel.hidden = false;
  // Open to peek on phone-sized viewports so the highlighted river
  // stays visible (TroutRoutes-style). Desktop keeps the side-drawer
  // behavior; the snap classes are scoped to a mobile media query so
  // they're inert on wider screens.
  const isMobile = window.matchMedia("(max-width: 700px)").matches;
  panel.classList.remove("peek", "full");
  requestAnimationFrame(() => {
    panel.classList.add("open");
    panel.classList.add(isMobile ? "peek" : "full");
  });
  _lastPanelOpenTs = Date.now();
  wireTrend(body);
  wireCatch(body, river);
  autoLoadFlowChart(body);
  highlightRiver(layer, baseStyle);
  refreshIcons();
}

function closeRiverPanel() {
  const panel = document.getElementById("river-panel");
  if (!panel || panel.hidden) return;
  panel.classList.remove("open", "peek", "full");
  _panelHideTimer = setTimeout(() => { panel.hidden = true; }, 240);
  clearRiverHighlight();
  clearStreamHighlight();
}

// Snap-sheet drag + tap handling on mobile. The grip is the only
// gesture surface; dragging it from peek up snaps to full, from full
// down snaps to peek, and dragging past a threshold below peek closes
// the panel. Tapping anywhere in the panel body (that isn't a button
// or other control) while at peek promotes to full so users can
// open the full sheet without precisely hitting the 5px grip.
// Shared snap-sheet wiring for bottom-sheet panels on mobile. Used by
// the river-detail panel and the unified controls panel.
//
// Handlers attached:
//   - pointer drag on the grip: follow finger, snap to peek/full on
//     release, drag-past-threshold dismisses (calls opts.onClose)
//   - tap on the body (not on a control) at peek: expand to full
//   - swipe on the body at peek: up -> full, down -> close
//   - tap on a tab label at peek: expand to full (if tabSelector given)
//
// All handlers no-op on desktop (the matchMedia gate); the panel's
// desktop CSS handles its own positioning.
function wireSnapSheet(panel, opts) {
  if (!panel) return null;
  const card = panel.querySelector(opts.cardSelector);
  const grip = panel.querySelector(opts.gripSelector);
  const body = panel.querySelector(opts.bodySelector);
  if (!card || !grip || !body) return null;
  const onClose = opts.onClose;
  const tabSelector = opts.tabSelector || null;

  let drag = null;
  const CLOSE_THRESHOLD_PX = 110;
  const SWIPE_THRESHOLD = 36;

  function cardHeight() { return card.getBoundingClientRect().height; }
  function isMobile() { return window.matchMedia("(max-width: 700px)").matches; }
  function setSnap(state) {
    panel.classList.remove("peek", "full");
    panel.classList.add(state);
  }

  function onDown(e) {
    if (!isMobile()) return;
    drag = {
      startY: e.clientY,
      lastY: e.clientY,
      // 62% translate at peek matches the CSS; full is 0.
      baseTranslate: panel.classList.contains("peek") ? 0.62 : 0,
    };
    card.classList.add("dragging");
    grip.setPointerCapture && grip.setPointerCapture(e.pointerId);
  }
  function onMove(e) {
    if (!drag) return;
    const dy = e.clientY - drag.startY;
    drag.lastY = e.clientY;
    const h = cardHeight();
    let px = drag.baseTranslate * h + dy;
    if (px < 0) px = 0;
    card.style.transform = `translateY(${px}px)`;
  }
  function onUp() {
    if (!drag) return;
    const dy = drag.lastY - drag.startY;
    const startedAtPeek = drag.baseTranslate > 0;
    card.style.transform = "";
    card.classList.remove("dragging");
    drag = null;
    if (startedAtPeek && dy > CLOSE_THRESHOLD_PX) { onClose(); return; }
    if (!startedAtPeek && dy > CLOSE_THRESHOLD_PX * 1.4) { setSnap("peek"); return; }
    if (dy < -30) setSnap("full");
    else if (dy > 30) setSnap("peek");
    else setSnap(startedAtPeek ? "full" : "peek");
  }
  grip.addEventListener("pointerdown", onDown);
  grip.addEventListener("pointermove", onMove);
  grip.addEventListener("pointerup", onUp);
  grip.addEventListener("pointercancel", onUp);

  body.addEventListener("click", (e) => {
    if (!isMobile()) return;
    if (!panel.classList.contains("peek")) return;
    if (e.target.closest("button, a, label, input, summary, select")) return;
    setSnap("full");
  });

  let bodyDrag = null;
  body.addEventListener("pointerdown", (e) => {
    if (!isMobile()) return;
    if (!panel.classList.contains("peek")) return;
    if (e.target.closest("button, a, label, input, summary, select")) return;
    bodyDrag = { startY: e.clientY };
  });
  body.addEventListener("pointermove", (e) => {
    if (!bodyDrag) return;
    bodyDrag.lastY = e.clientY;
  });
  body.addEventListener("pointerup", () => {
    if (!bodyDrag) return;
    const dy = (bodyDrag.lastY || bodyDrag.startY) - bodyDrag.startY;
    bodyDrag = null;
    if (Math.abs(dy) < SWIPE_THRESHOLD) return;
    if (dy < 0) setSnap("full");
    else onClose();
  });
  body.addEventListener("pointercancel", () => { bodyDrag = null; });

  if (tabSelector) {
    body.addEventListener("click", (e) => {
      if (!isMobile()) return;
      const tab = e.target.closest(tabSelector);
      if (!tab) return;
      if (panel.classList.contains("peek")) setSnap("full");
    });
  }

  return { setSnap };
}

// Wire the river panel's snap-sheet handlers.
wireSnapSheet(document.getElementById("river-panel"), {
  cardSelector: ".river-panel-card",
  gripSelector: ".river-panel-grip",
  bodySelector: "#river-panel-body",
  tabSelector: ".bl-tab",
  onClose: closeRiverPanel,
});

async function autoLoadFlowChart(root) {
  const box = root.querySelector(".bl-flow-chart[data-site]");
  if (!box || box.dataset.loaded) return;
  box.dataset.loaded = "1";
  const site = box.getAttribute("data-site");
  box.innerHTML = '<div class="bl-trend-msg">Loading flow chart&hellip;</div>';
  try {
    const d = await fetch(
      `/api/history?site_no=${encodeURIComponent(site)}`
    ).then((r) => r.json());
    box.innerHTML = sparkline(d.series);
    wireSparkHover(box);
  } catch (_) {
    box.innerHTML = '<div class="bl-trend-msg">Flow chart unavailable.</div>';
  }
}

function highlightRiver(layer, baseStyle) {
  clearRiverHighlight();
  if (!layer || !layer.setStyle) return;
  _selectedRiver = { layer, base: baseStyle || null };
  layer.setStyle({ weight: 8, opacity: 0.95 });
}

function clearRiverHighlight() {
  if (_selectedRiver && _selectedRiver.layer.setStyle && _selectedRiver.base) {
    _selectedRiver.layer.setStyle(_selectedRiver.base);
  }
  _selectedRiver = null;
}

function wireRiverPanel() {
  const panel = document.getElementById("river-panel");
  if (!panel) return;
  panel.querySelectorAll("[data-close]").forEach((el) =>
    el.addEventListener("click", closeRiverPanel));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeRiverPanel();
  });
  // Clicking empty map closes the panel. Guarded so the same click that
  // opened it (via a layer) doesn't immediately close it.
  map.on("click", () => {
    if (Date.now() - _lastPanelOpenTs > 300) closeRiverPanel();
  });
}

// -- Clickable-stream network (Phase B) ------------------------------

const STREAM_MIN_ZOOM = 9;          // below this, viewport is too large
const STREAM_CLASS_COLORS = {
  class_a: "#b8860b", wilderness: "#117a65", wild_reproduction: "#1e8449",
  stocked: "#2c6fbf", designated: "#27ae60",
};
const STREAM_CLASS_LABEL = {
  class_a: "Class A wild trout", wilderness: "Wilderness trout",
  wild_reproduction: "Wild reproduction", stocked: "Stocked trout",
  designated: "Designated trout",
};
// "trout" colors by class; "conditions" greys the network so the
// gauged condition colors (green/yellow/red) read on top (Decision C).
let streamColorMode = "trout";

function streamColor(p) {
  if (streamColorMode === "conditions") return "#9aa7b8";
  return STREAM_CLASS_COLORS[p.trout_class] || "#8a9bb0";
}
function streamWeight(p) {
  // Floor of 4px keeps even order-1 headwaters a tappable target (a 2px
  // line is nearly impossible to hit on touch). Scales up with order.
  return Math.max(4, Math.min(7, (p.streamorder || 3) * 0.9));
}
function streamStyle(p) {
  return { color: streamColor(p), weight: streamWeight(p), opacity: 0.8 };
}

let _streamReqId = 0;
// Names + levelpathids currently rendered in clickableVisible. The
// dot-suppression logic below uses these to skip a gauge marker when
// the same river is already reachable as a clickable line -- avoids
// the "Antietam Creek dot on an unnamed tributary" surprise and the
// Susquehanna's redundant dot-over-line.
let _loadedClkNames = new Set();
let _loadedClkLpids = new Set();

async function loadClickableStreams() {
  if (!map.hasLayer(clickableLayer)) return;       // toggled off
  if (map.getZoom() < STREAM_MIN_ZOOM) {
    // Earlier versions cleared the layer here. That made panning
    // across the zoom-9 boundary blink the entire stream network
    // off and back on. Now we just no-op; the previous frame's
    // features linger until the next moveend at zoom 9+ replaces
    // them. The bbox-area guard below + the per-zoom StreamOrder
    // filter still prevent country-scale fetches when the user is
    // way zoomed out.
    return;
  }
  const b = map.getBounds();
  // 4° cap (was 6°): at zoom 9 a 6° bbox is already country-scale on
  // tall screens and a fast pinch-out can briefly cross the guard,
  // firing a fetch that returns tens of thousands of features and
  // locks the main thread.
  if (b.getEast() - b.getWest() > 4 || b.getNorth() - b.getSouth() > 4) return;
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((x) => x.toFixed(4)).join(",");
  const reqId = ++_streamReqId;
  try {
    const fc = await fetch(
      `/api/clickable_streams?bbox=${bbox}&zoom=${map.getZoom()}`
    ).then((r) => r.json());
    if (reqId !== _streamReqId) return;            // a newer move superseded us
    clickableVisible.clearLayers(); clickableHit.clearLayers();
    clickableVisible.addData(fc); clickableHit.addData(fc);
    if (_selStreamKey != null) _paintHighlight(_selStreamKey);
    _loadedClkNames = new Set();
    _loadedClkLpids = new Set();
    for (const feat of (fc.features || [])) {
      const p = feat.properties || {};
      if (p.gnis_name) _loadedClkNames.add(p.gnis_name.trim().toLowerCase());
      if (p.levelpathid != null) _loadedClkLpids.add(p.levelpathid);
    }
    renderRivers();          // dot vs line decision depends on these sets
  } catch (_) { /* transient; next moveend retries */ }
}

// True when this river has at least one reach currently drawn in the
// clickable network -- if so, the user can already click the line to
// open the river panel, so a redundant gauge dot adds noise (and, for
// rivers like Antietam Creek, lands the dot on an unrelated tributary
// at the gauge-centroid). Falls back to false when the clickable layer
// is off or zoomed below STREAM_MIN_ZOOM -- the dot is the only access
// point then.
function _riverHasClickableReach(r) {
  if (!map.hasLayer(clickableLayer)) return false;
  if (r.name && _loadedClkNames.has(r.name.trim().toLowerCase())) return true;
  if (Array.isArray(r.levelpathids)) {
    for (const lpid of r.levelpathids) {
      if (_loadedClkLpids.has(lpid)) return true;
    }
  }
  return false;
}

function restyleStreams() {
  clickableVisible.setStyle((f) => streamStyle(f.properties));
  if (_selStreamKey != null) _paintHighlight(_selStreamKey);
}

// Highlight every rendered reach sharing the clicked stream's *named-
// river* identity (or LevelPathID for unnamed reaches). NHDPlusV2
// frequently splits a single named river across multiple levelpathids
// at HUC boundaries, so matching on lpid alone leaves the upper and
// lower reaches of e.g. "Little Conestoga Creek" unhighlighted; the
// composite key falls back to lpid only when the clicked reach has no
// gnis_name (true for ~1/3 of order-1 headwaters). `_paintHighlight`
// is idempotent so it can be re-applied after the clickable-streams
// layer is rebuilt (pan/zoom moveend) or restyled (trout/conditions
// mode toggle) -- without it, the red selection silently reverts to
// the base style as soon as you pan the map. The repaint-after-fetch
// path is what makes the highlight continue onto newly-loaded
// segments of the same named river as the user pans along its reach.
let _selStreamKey = null;
function _featMatchesKey(f, key) {
  if (!f || !key) return false;
  if (key.name) return _normName(f.properties.gnis_name) === key.name;
  return f.properties.levelpathid === key.lpid;
}
function _paintHighlight(key) {
  clickableVisible.eachLayer((l) => {
    if (_featMatchesKey(l.feature, key)) {
      l.setStyle({ weight: 8, color: "#e74c3c", opacity: 0.95 });
    }
  });
}
function highlightStream(p) {
  clearStreamHighlight();
  const name = _normName(p && p.gnis_name);
  _selStreamKey = { name: name || null, lpid: (p && p.levelpathid) ?? null };
  _paintHighlight(_selStreamKey);
}
function clearStreamHighlight() {
  if (_selStreamKey == null) return;
  clickableVisible.eachLayer((l) => {
    if (l.feature) l.setStyle(streamStyle(l.feature.properties));
  });
  _selStreamKey = null;
}

function _normName(s) { return (s || "").trim().toLowerCase(); }

// A clickable-network reach belongs to a gauged river when a loaded river
// shares either its GNIS name OR its NHD levelpath. Levelpath matching
// catches reaches that NHD and NLDI label differently for the same
// physical river (e.g., where NHD names a downstream tidal section
// "Gunpowder River" and an upstream section "Gunpowder Falls" on the
// same levelpath). When several rivers match, pick the one whose
// representative point is nearest the click.
function _gaugedRiverFor(p, latlng) {
  const name = _normName(p.gnis_name);
  const lpid = p.levelpathid;
  if (!name && lpid == null) return null;
  // Search BOTH the current set (viewport rivers when zoomed in) and the
  // full state snapshot, deduped by site_no. Without the stateRivers
  // fallback, clicking an upstream reach of a river whose gauges sit
  // outside the current bbox (e.g., the Gunpowder above Glencoe) wouldn't
  // find a match and would wrongly render as ungauged.
  const seen = new Set();
  const matches = [];
  for (const list of [allRivers, stateRivers]) {
    if (!list) continue;
    for (const r of list) {
      if (!r.site_no || seen.has(r.site_no)) continue;
      const nameMatch = name && _normName(r.name) === name;
      const lpidMatch = lpid != null && Array.isArray(r.levelpathids)
        && r.levelpathids.includes(lpid);
      if (nameMatch || lpidMatch) {
        seen.add(r.site_no); matches.push(r);
      }
    }
  }
  if (matches.length <= 1 || !latlng) return matches[0] || null;
  let best = matches[0], bestD = Infinity;
  for (const r of matches) {
    const dy = r.lat - latlng.lat, dx = r.lon - latlng.lng;
    const d = dy * dy + dx * dx;
    if (d < bestD) { bestD = d; best = r; }
  }
  return best;
}

function onStreamClick(p, latlng) {
  highlightStream(p);                // whole-river emphasis, gauged or not
  // Unify the two layers: if this reach is part of a gauged river, open
  // that river's rich panel instead of the generic ungauged card, so the
  // whole river behaves as one thing regardless of where you click.
  const gauged = _gaugedRiverFor(p, latlng);
  if (gauged) { openRiverPanel(gauged, null, null); return; }
  const panel = document.getElementById("river-panel");
  const body = document.getElementById("river-panel-body");
  clearTimeout(_panelHideTimer);
  const cls = p.trout_class;
  const label = STREAM_CLASS_LABEL[cls] || "No trout designation";
  body.innerHTML =
    `<div class="bl-card"><div class="bl-card-head">` +
    `<div style="font-size:18px;font-weight:700;color:#1a1a2e">${esc(p.gnis_name || "Unnamed stream")}</div>` +
    `<span class="stream-badge" style="background:${esc(streamColor(p))}">${esc(label)}</span>` +
    `<span class="stream-badge" style="background:#64748b">Order ${esc(p.streamorder)}</span>` +
    `<div class="bl-summary">Ungauged reach &mdash; no live USGS flow here. Showing what we know.</div>` +
    `</div><div class="bl-card-body">` +
    `<div class="bl-catch-cta"></div>` +
    `<details class="bl-section bl-hatch"><summary>Hatching now</summary>` +
    `<div class="bl-section-body">Hatch guidance for this reach's zone lands with the ungauged-card data wire-up.</div></details>` +
    `<details class="bl-section" open><summary>Trout</summary>` +
    `<div class="bl-section-body">${esc(label)}${cls ? " (state designation)" : ""}.</div></details>` +
    `<details class="bl-section"><summary>Access &amp; land</summary>` +
    `<div class="bl-section-body">Access points + public/private land coming in the TroutRoutes-depth phase.</div></details>` +
    `<details class="bl-section"><summary>Conditions</summary>` +
    `<div class="bl-section-body">No gauge on this reach. Nearest downstream gauge could provide context.</div></details>` +
    `</div></div>`;
  body.scrollTop = 0;
  panel.hidden = false;
  requestAnimationFrame(() => panel.classList.add("open"));
  _lastPanelOpenTs = Date.now();
  // Catch CTA: the clicked point is a reasonable catch location for an
  // ungauged stream (no representative gauge to attach to).
  wireCatch(body, {
    name: p.gnis_name, site_no: null,
    lat: latlng ? latlng.lat : null, lon: latlng ? latlng.lng : null,
  });
}

// Coloring-mode toggle (Decision C): Conditions vs Trout class.
// Wiring lives with the unified filter popover, so it's just a DOM
// segmented control instead of its own Leaflet control. Both modes
// re-style the same clickable network -- it's a viewing aid, not a
// filter, so it groups under "Show on map" rather than "Show rivers".
document.querySelectorAll("#color-mode button").forEach((b) =>
  b.addEventListener("click", () => {
    streamColorMode = b.dataset.mode;
    document.querySelectorAll("#color-mode button").forEach((x) =>
      x.classList.toggle("on", x === b));
    restyleStreams();
  }));

// Re-fetch the network whenever the view settles or the layer is toggled.
let _streamTimer = null;
let _publicLandsTimer = null;
// 500ms (was 350ms) so touch-device momentum-pans don't fire two
// fetches per gesture -- iOS Safari and Android Chrome both emit a
// moveend at the start of the deceleration *and* at the rest point.
map.on("moveend", () => {
  clearTimeout(_streamTimer);
  _streamTimer = setTimeout(loadClickableStreams, 500);
  clearTimeout(_publicLandsTimer);
  _publicLandsTimer = setTimeout(loadPublicLands, 500);
});

// -- Gauges --

function populateHatchOptions() {
  const sel = document.getElementById("hatch-select");
  const cur = sel.value;
  const set = new Set();
  allRivers.forEach((r) => (r.active_hatches || []).forEach((h) => set.add(h)));
  const insects = [...set].sort();
  sel.innerHTML =
    '<option value="any">Any</option>' +
    '<option value="active">Active now</option>' +
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

// Exactly one clickable representation per river:
//   1. The NLDI flowline if loaded (riverLinesLayer), else
//   2. Skip if the clickable-stream network already draws this river
//      somewhere in the viewport -- the user can click the line, so a
//      gauge dot here would just be a redundant target landing on the
//      gauge centroid (which can fall on an unrelated tributary), else
//   3. A pin (fallback for low zoom / clickable layer off / no matching
//      NHD reach, e.g. tiny named creeks the network filters out).
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
    if (_riverHasClickableReach(r)) continue;  // clickable network has it
    const m = L.marker([r.lat, r.lon], { icon: makeConditionIcon(r.overall) });
    const tBadge = r.on_trout
      ? ' <span style="color:var(--bl-trout);font-size:11px">Trout</span>'
      : "";
    const sBadge = r.near_stocked
      ? ' <span style="color:var(--bl-stocked);font-size:11px">Stocked</span>'
      : "";
    m.bindTooltip(
      `<b>${esc(r.name)}</b>${tBadge}${sBadge}` +
      `<br><span style="color:${r.color}">${esc(r.label)}</span>`
    );
    m._blRiver = r;
    m.on("click", () => openRiverPanel(r, m, null));
    riversLayer.addLayer(m);
  }
}

// Shape-coded condition marker. Color + shape so colorblind anglers
// get the same signal: filled disc for Good, filled + center dot for
// Fair, filled + horizontal bar for Poor, dashed outline for No data.
// CSS styling for the four variants lives in app.css under .marker*.
const CONDITION_VARIANT = {
  green: "good",
  yellow: "fair",
  red: "poor",
  gray: "none",
};
function makeConditionIcon(overall) {
  const variant = CONDITION_VARIANT[overall] || "none";
  return L.divIcon({
    className: "condition-marker-wrap",
    html: `<div class="marker marker--${variant}"></div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
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

let accessLoadedState = null;
let accessLoading = false;

function makeAccessIcon(type) {
  const meta = ACCESS_TYPE_META[type] || ACCESS_TYPE_META.walk_in;
  return L.divIcon({
    className: "access-marker",
    html: `<div class="access-marker-pin" style="background:${meta.color}">`
        + `${esc(meta.glyph)}</div>`,
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function accessPopupHtml(p) {
  const accessChip = p.access
    ? `<span class="ap-chip ap-chip-${esc(p.access)}">${esc(p.access)}</span>`
    : "";
  const typeLabel = String(p.type || "walk_in").replace(/_/g, " ");
  const notes = p.notes ? `<div class="ap-notes">${esc(p.notes)}</div>` : "";
  const link = p.agency_url
    ? `<div class="ap-link"><a href="${esc(p.agency_url)}" target="_blank" `
    + `rel="noopener noreferrer">Agency info &rarr;</a></div>`
    : "";
  return `<div class="ap-popup">`
    + `<div class="ap-name">${esc(p.name || "Access point")}</div>`
    + `<div class="ap-meta">${esc(typeLabel)}${accessChip}</div>`
    + notes
    + link
    + `</div>`;
}

// Public-lands fetch: bbox-bound, debounced on moveend, zoom-gated.
// Mirrors loadClickableStreams contract: skip when toggled off, skip
// at country-scale bboxes, replace the layer's contents wholesale on
// each fetch (parcels are sparse enough at zoom 8+ that we don't need
// the streams' "merge-with-loaded" gymnastics).
// Matches STREAM_MIN_ZOOM and RIVER_LINE_MIN_ZOOM so all three layer
// families appear/hide at the same zoom boundary -- previous staggered
// 8/9/9 setup made the user see two distinct "snap" moments while
// zooming through 8 -> 9.
const PUBLIC_LANDS_MIN_ZOOM = 9;
let _publicLandsReqId = 0;
async function loadPublicLands() {
  if (!map.hasLayer(publicLandsLayer)) return;
  if (map.getZoom() < PUBLIC_LANDS_MIN_ZOOM) {
    // Don't clear here: see the matching note in loadClickableStreams.
    // Letting the previous frame's parcels linger across the
    // zoom-threshold boundary eliminates the blink-off-blink-on
    // flash users hit when pinching from zoom 10 to zoom 8.
    return;
  }
  const b = map.getBounds();
  // 4° cap matching loadClickableStreams.
  if (b.getEast() - b.getWest() > 4 || b.getNorth() - b.getSouth() > 4) return;
  const bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((x) => x.toFixed(4)).join(",");
  const reqId = ++_publicLandsReqId;
  try {
    const fc = await fetch(
      `/api/public_lands?bbox=${bbox}&zoom=${map.getZoom()}`
    ).then((r) => r.json());
    if (reqId !== _publicLandsReqId) return;        // newer pan superseded us
    publicLandsLayer.clearLayers();
    publicLandsLayer.addData(fc);
  } catch (_) { /* transient; next moveend retries */ }
}

async function ensureAccess(state) {
  if (accessLoadedState === state || accessLoading) return;
  accessLoading = true;
  try {
    const fc = await fetch(`/api/access?state=${state}`).then((r) => r.json());
    accessLayer.clearLayers();
    for (const f of (fc.features || [])) {
      const c = (f.geometry && f.geometry.coordinates) || null;
      const p = f.properties || {};
      if (!c || c.length < 2) continue;
      const m = L.marker([c[1], c[0]], { icon: makeAccessIcon(p.type) });
      m.bindPopup(accessPopupHtml(p), popupOpts());
      accessLayer.addLayer(m);
    }
    accessLoadedState = state;
  } catch (_) {
    /* leave layer empty; user can re-toggle to retry */
  } finally {
    accessLoading = false;
  }
}

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
      line._blRiver = r;
      line.on("click", () => openRiverPanel(
        r, line, { color: r.color, weight: 5, opacity: 0.6 }));
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
    if (r) {
      line._blRiver = r;
      line.on("click", () => openRiverPanel(
        r, line, { color, weight: 5, opacity: 0.6 }));
    }
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
  // Refresh trout / access for the new state only if the layer is currently shown.
  troutLoadedState = null;
  if (map.hasLayer(troutLayer)) ensureTrout(s);
  accessLoadedState = null;
  if (map.hasLayer(accessLayer)) ensureAccess(s);
};

// -- Unified controls panel: Layers / Filters / Legend in one tabbed sheet
// Three header buttons (#ctrl-layers, #ctrl-filters, #ctrl-legend) act as
// direct-entry points. Each button opens the panel to its tab; clicking
// the same button again closes the panel; clicking a different button
// while the panel is open switches the active tab.

const controlsPanel = document.getElementById("controls-panel");
const _ctrlTabRadios = {
  layers: document.getElementById("ctrl-tab-layers"),
  filters: document.getElementById("ctrl-tab-filters"),
  legend: document.getElementById("ctrl-tab-legend"),
};
const _ctrlHeaderBtns = {
  layers: document.getElementById("ctrl-layers"),
  filters: document.getElementById("ctrl-filters"),
  legend: document.getElementById("ctrl-legend"),
};
let _ctrlActiveTab = "layers";
let _ctrlHideTimer = null;

function _setCtrlHeaderActive(tab) {
  for (const [t, btn] of Object.entries(_ctrlHeaderBtns)) {
    const on = t === tab && !controlsPanel.hidden && controlsPanel.classList.contains("open");
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-expanded", on ? "true" : "false");
  }
}

function _selectCtrlTab(tab) {
  const radio = _ctrlTabRadios[tab];
  if (radio) radio.checked = true;
  _ctrlActiveTab = tab;
  _setCtrlHeaderActive(tab);
}

function openControlsPanel(tab) {
  clearTimeout(_ctrlHideTimer);
  _selectCtrlTab(tab);
  controlsPanel.hidden = false;
  const isMobile = window.matchMedia("(max-width: 700px)").matches;
  controlsPanel.classList.remove("peek", "full");
  requestAnimationFrame(() => {
    controlsPanel.classList.add("open");
    if (isMobile) controlsPanel.classList.add("peek");
    _setCtrlHeaderActive(tab);
  });
}

function closeControlsPanel() {
  if (!controlsPanel || controlsPanel.hidden) return;
  controlsPanel.classList.remove("open", "peek", "full");
  _ctrlHideTimer = setTimeout(() => { controlsPanel.hidden = true; }, 240);
  _setCtrlHeaderActive(null);
}

// Header buttons -> open the panel directly to their tab.
for (const [tab, btn] of Object.entries(_ctrlHeaderBtns)) {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !controlsPanel.hidden && controlsPanel.classList.contains("open");
    if (open && _ctrlActiveTab === tab) {
      closeControlsPanel();
    } else if (open) {
      _selectCtrlTab(tab);
      // On mobile a tab switch while at peek means the user is engaging
      // with the panel; promote to full (same rule as the river panel).
      if (window.matchMedia("(max-width: 700px)").matches &&
          controlsPanel.classList.contains("peek")) {
        controlsPanel.classList.remove("peek");
        controlsPanel.classList.add("full");
      }
    } else {
      openControlsPanel(tab);
    }
  });
}

// X button and backdrop -> close.
controlsPanel.querySelectorAll("[data-close]").forEach((el) =>
  el.addEventListener("click", closeControlsPanel));

// ESC closes from any state.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !controlsPanel.hidden) closeControlsPanel();
});

// Click-outside closes (desktop popover behavior).
document.addEventListener("click", (e) => {
  if (controlsPanel.hidden) return;
  if (controlsPanel.contains(e.target)) return;
  if (Object.values(_ctrlHeaderBtns).some((b) => b.contains(e.target))) return;
  closeControlsPanel();
});

// In-panel tab switching (clicking a tab label inside the panel)
// updates the header's active-state indicator.
for (const [tab, radio] of Object.entries(_ctrlTabRadios)) {
  radio.addEventListener("change", () => {
    if (radio.checked) {
      _ctrlActiveTab = tab;
      _setCtrlHeaderActive(tab);
    }
  });
}

// Snap-sheet behavior on mobile (bottom-sheet with peek/full).
wireSnapSheet(controlsPanel, {
  cardSelector: ".controls-panel-card",
  gripSelector: ".controls-panel-grip",
  bodySelector: "#controls-panel-body",
  tabSelector: ".ctrl-tab",
  onClose: closeControlsPanel,
});

// Layer visibility: persist last user choice across page loads in
// localStorage["bl_layers"]. Defaults come from the HTML `checked`
// attribute when no saved preference exists -- new layers added after
// a user's last visit will use whatever default the HTML declares.
const LAYER_PREF_KEY = "bl_layers";
function loadLayerPrefs() {
  try {
    const raw = localStorage.getItem(LAYER_PREF_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch (_) { return {}; }
}
function saveLayerPref(id, on) {
  try {
    const prefs = loadLayerPrefs();
    prefs[id] = !!on;
    localStorage.setItem(LAYER_PREF_KEY, JSON.stringify(prefs));
  } catch (_) {}
}
const _layerPrefs = loadLayerPrefs();

// Layer visibility checkboxes -- one per Leaflet layer. Toggling the
// checkbox mirrors what the old L.control.layers did, plus fires the
// same side-effects (lazy load of clickable streams / trout / access).
function wireLayerToggle(id, layer, onAdd) {
  const cb = document.getElementById(id);
  // Apply saved preference if present, otherwise leave the HTML default.
  if (Object.prototype.hasOwnProperty.call(_layerPrefs, id)) {
    cb.checked = !!_layerPrefs[id];
  }
  if (cb.checked && !map.hasLayer(layer)) {
    map.addLayer(layer);
    if (onAdd) onAdd();
  } else if (!cb.checked && map.hasLayer(layer)) {
    map.removeLayer(layer);
  }
  cb.addEventListener("change", () => {
    if (cb.checked) {
      map.addLayer(layer);
      if (onAdd) onAdd();
    } else {
      map.removeLayer(layer);
    }
    saveLayerPref(id, cb.checked);
  });
}
wireLayerToggle("lyr-fishable", clickableLayer, loadClickableStreams);
// Toggling the clickable layer off needs to bring the gauge dots back
// (since _riverHasClickableReach now returns false), so re-render.
document.getElementById("lyr-fishable").addEventListener("change", (e) => {
  if (!e.target.checked) renderRivers();
});
wireLayerToggle("lyr-usgs", hydroLayer);
wireLayerToggle("lyr-trout", troutLayer, () => ensureTrout(currentSt));
wireLayerToggle("lyr-access", accessLayer, () => ensureAccess(currentSt));
wireLayerToggle("lyr-public-lands", publicLandsLayer, loadPublicLands);
wireLayerToggle("lyr-pins", pinsLayer);

// Base-map segmented control: mutually exclusive radio behavior.
const basemapSeg = document.getElementById("basemap-mode");
if (basemapSeg) {
  // Reflect the loaded preference on the segment buttons.
  for (const btn of basemapSeg.querySelectorAll("button[data-base]")) {
    btn.classList.toggle("on", btn.dataset.base === currentBaseKey);
    btn.addEventListener("click", () => {
      const key = btn.dataset.base;
      setBaseMap(key);
      for (const sib of basemapSeg.querySelectorAll("button[data-base]")) {
        sib.classList.toggle("on", sib.dataset.base === key);
      }
    });
  }
}

document.getElementById("filter-reset").onclick = () => {
  document.getElementById("cond-select").value = "any";
  document.getElementById("hatch-select").value = "any";
  document.getElementById("trout-only").checked = false;
  document.getElementById("stocked-only").checked = false;
  onFilterChange();
};

// Legend now lives inside the unified controls panel above (the Legend
// tab). The bottom-left pill + bl_legend_open localStorage key are
// retired; the panel's own open state is the single source of truth.

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
