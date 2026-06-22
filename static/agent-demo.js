/* BlueLiner agent demo panel — LOCAL DEMO ONLY, self-gating.
 *
 * This script ships in the static bundle, so the PUBLIC deployment serves it
 * too. That is safe by design: on boot it calls GET /api/agent/health and does
 * NOTHING unless the server reports {enabled:true}. The public app never mounts
 * the agent router (the route 404s) and never sets AGENT_DEMO_ENABLED, so the
 * panel stays invisible there. It only comes alive against agent/demo_server.py
 * running locally with the flag on. No API key ever reaches the browser — the
 * browser only ever talks to the local server's /api/agent/* endpoints.
 *
 * Plain JS, no bundler: it drives the live MapLibre map via window.map using
 * only addSource/addLayer/getSource (the maplibregl constructor isn't on
 * window), and renders the agent's reasoning inside the shared left-rail panel
 * (#controls-panel) — it fills the empty #bl-agent-root pane and reveals the
 * hidden "Agent" rail/mobile tabs, which controls.ts has already wired.
 */
(function () {
  "use strict";

  var COND_COLOR = { green: "#4A8C5C", yellow: "#B7892F", red: "#B3473B", gray: "#7F8B9C" };
  var PROSPECT_COLOR = "#7A3DB8"; // distinct from the app's condition palette

  var TOKEN_KEY = "bl_agent_token";
  var state = { health: null, busy: false };

  // --------------------------------------------------------------------
  // Boot: gate on /api/agent/health
  // --------------------------------------------------------------------
  function boot() {
    fetch("/api/agent/health", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : { enabled: false }; })
      .then(function (h) {
        if (!h || !h.enabled) return; // public deployment / flag off -> stay hidden
        state.health = h;
        renderPanel(h);
      })
      .catch(function () { /* no endpoint -> not a demo build; stay hidden */ });
  }

  // --------------------------------------------------------------------
  // Map helpers (MapLibre via window.map)
  // --------------------------------------------------------------------
  function map() { return window.map; }

  function whenStyleReady(fn) {
    var m = map();
    if (!m) { setTimeout(function () { whenStyleReady(fn); }, 200); return; }
    if (m.isStyleLoaded()) { fn(m); } else { m.once("load", function () { fn(m); }); }
  }

  function setGeoJSON(id, data, addLayers) {
    whenStyleReady(function (m) {
      var src = m.getSource(id);
      if (src) { src.setData(data); return; }
      m.addSource(id, { type: "geojson", data: data });
      addLayers(m);
    });
  }

  function clearLayer(srcId, layerIds) {
    var m = map();
    if (!m || !m.getSource) return;
    layerIds.forEach(function (l) { if (m.getLayer(l)) m.removeLayer(l); });
    if (m.getSource(srcId)) m.removeSource(srcId);
  }

  function fitTo(points) {
    var m = map();
    if (!m || !points.length) return;
    if (points.length === 1) { m.flyTo({ center: points[0], zoom: 11 }); return; }
    var b = points.reduce(function (acc, p) {
      return [Math.min(acc[0], p[0]), Math.min(acc[1], p[1]),
              Math.max(acc[2], p[0]), Math.max(acc[3], p[1])];
    }, [180, 90, -180, -90]);
    m.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 80, maxZoom: 11, duration: 800 });
  }

  // --------------------------------------------------------------------
  // Networking
  // --------------------------------------------------------------------
  function post(path, body) {
    var headers = { "Content-Type": "application/json", Accept: "application/json" };
    if (state.health && state.health.token_required) {
      headers["X-Agent-Demo-Token"] = localStorage.getItem(TOKEN_KEY) || "";
    }
    return fetch(path, { method: "POST", headers: headers, body: JSON.stringify(body) })
      .then(function (r) {
        return r.json().then(function (j) {
          if (!r.ok) throw new Error(j.detail || ("HTTP " + r.status));
          return j;
        });
      });
  }

  function currentState() {
    var sel = document.getElementById("state-select");
    return sel && sel.value ? sel.value : "MD";
  }

  function mapCenter() {
    var c = map() && map().getCenter ? map().getCenter() : null;
    return c ? { lat: +c.lat.toFixed(4), lng: +c.lng.toFixed(4) } : { lat: 39.63, lng: -76.68 };
  }

  // --------------------------------------------------------------------
  // Trip planner
  // --------------------------------------------------------------------
  function runPlan() {
    if (state.busy) return;
    var c = mapCenter();
    var prefs = (document.getElementById("bl-agent-prefs") || {}).value || "";
    var orch = (document.querySelector('input[name="bl-agent-orch"]:checked') || {}).value || "hand";
    setBusy(true, "Planning — gathering live USGS/NOAA, ranking, running safety guardrails…");
    post("/api/agent/plan", { lat: c.lat, lng: c.lng, state: currentState(),
                              preferences: prefs, version: 3, orchestrator: orch })
      .then(renderPlan)
      .catch(showError)
      .finally(function () { setBusy(false); });
  }

  function renderPlan(res) {
    var recs = res.recommendations || [];
    var blocked = res.blocked || [];
    var pts = [];
    var features = [];
    recs.forEach(function (r, i) {
      if (typeof r.lng === "number" && typeof r.lat === "number") {
        pts.push([r.lng, r.lat]);
        features.push(pt(r.lng, r.lat, { kind: "rec", rank: i + 1,
          color: COND_COLOR[r.overall_score] || COND_COLOR.gray }));
      }
    });
    blocked.forEach(function (b) {
      if (typeof b.lng === "number" && typeof b.lat === "number") {
        features.push(pt(b.lng, b.lat, { kind: "blocked", color: COND_COLOR.red }));
      }
    });

    // Colored discs only — no symbol/text layers (avoids any glyph-font
    // dependency on the base style). The panel cards carry the rank numbers;
    // clicking a card flies the map to its marker.
    clearLayer("agent-recs", ["agent-recs-circle"]);
    setGeoJSON("agent-recs", fc(features), function (m) {
      m.addLayer({ id: "agent-recs-circle", type: "circle", source: "agent-recs",
        paint: { "circle-radius": 9, "circle-color": ["get", "color"],
                 "circle-stroke-width": 2, "circle-stroke-color": "#fff",
                 "circle-opacity": ["case", ["==", ["get", "kind"], "blocked"], 0.55, 0.95] } });
    });
    fitTo(pts);

    var html = "";
    html += metaBar(res);
    if (!recs.length) html += '<div class="bl-agent-empty">No safe recommendation for these inputs.</div>';
    recs.forEach(function (r, i) {
      var why = (r.why || []).map(function (w) { return "<li>" + esc(w) + "</li>"; }).join("");
      html += '<div class="bl-agent-card" data-lng="' + r.lng + '" data-lat="' + r.lat + '">'
        + '<div class="bl-agent-card-h"><span class="bl-agent-dot" style="background:'
        + (COND_COLOR[r.overall_score] || COND_COLOR.gray) + '">' + (i + 1) + '</span>'
        + '<b>' + esc(r.name || r.river_id) + '</b>'
        + '<span class="bl-agent-conf">' + esc(r.confidence || "") + '</span></div>'
        + '<div class="bl-agent-verdict">' + esc(r.verdict || "") + '</div>'
        + (why ? '<ul class="bl-agent-why">' + why + "</ul>" : "")
        + "</div>";
    });
    if (blocked.length) {
      html += '<div class="bl-agent-sub">Blocked by guardrails</div>';
      blocked.forEach(function (b) {
        html += '<div class="bl-agent-blocked">⛔ <b>' + esc(b.name || b.river_id)
          + "</b> — " + esc(b.reason) + "</div>";
      });
    }
    if (res.notes) html += '<div class="bl-agent-notes">' + esc(res.notes) + "</div>";
    setResults(html);
    wireCardClicks();
  }

  // --------------------------------------------------------------------
  // Prospector
  // --------------------------------------------------------------------
  function runDiscover() {
    if (state.busy) return;
    setBusy(true, "Discovering — scoring undesignated reaches on topology, flow & access…");
    post("/api/agent/discover", { states: currentState(), shortlist_k: 8 })
      .then(renderDiscover)
      .catch(showError)
      .finally(function () { setBusy(false); });
  }

  function renderDiscover(res) {
    var prospects = res.prospects || [];
    var pts = [], ptFeats = [], lineFeats = [];
    prospects.forEach(function (p, i) {
      if (typeof p.lng === "number" && typeof p.lat === "number") {
        pts.push([p.lng, p.lat]);
        ptFeats.push(pt(p.lng, p.lat, { rank: i + 1 }));
      }
      if (p.line && p.line.length > 1) {
        // p.line is already [lng, lat] (GeoJSON/MapLibre native) — pass through.
        lineFeats.push({ type: "Feature",
          geometry: { type: "LineString", coordinates: p.line }, properties: {} });
      }
    });

    clearLayer("agent-prospects", ["agent-prospects-circle"]);
    clearLayer("agent-prospect-lines", ["agent-prospect-lines-line"]);
    setGeoJSON("agent-prospect-lines", fc(lineFeats), function (m) {
      m.addLayer({ id: "agent-prospect-lines-line", type: "line", source: "agent-prospect-lines",
        paint: { "line-color": PROSPECT_COLOR, "line-width": 4, "line-opacity": 0.8,
                 "line-dasharray": [2, 1.5] } });
    });
    setGeoJSON("agent-prospects", fc(ptFeats), function (m) {
      m.addLayer({ id: "agent-prospects-circle", type: "circle", source: "agent-prospects",
        paint: { "circle-radius": 9, "circle-color": PROSPECT_COLOR,
                 "circle-stroke-width": 2, "circle-stroke-color": "#fff" } });
    });
    fitTo(pts);

    var html = "";
    html += '<div class="bl-agent-meta">'
      + '<span>' + prospects.length + " prospects</span>"
      + (res.excluded ? "<span>" + res.excluded.length + " excluded</span>" : "")
      + (res.usage ? "<span>$" + (res.usage.est_cost_usd || 0) + "</span>" : "")
      + "</div>";
    html += '<div class="bl-agent-lede">Undesignated reaches that look fishable — ranked '
      + "by proximity-to-trout topology, flow size & access. Held-out backtest validated.</div>";
    if (!prospects.length) html += '<div class="bl-agent-empty">No prospect cleared the confidence floor.</div>';
    prospects.forEach(function (p, i) {
      var ev = (p.evidence || []).map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("");
      var conf = typeof p.confidence === "number" ? p.confidence.toFixed(2) : esc(p.confidence || "");
      html += '<div class="bl-agent-card" data-lng="' + p.lng + '" data-lat="' + p.lat + '">'
        + '<div class="bl-agent-card-h"><span class="bl-agent-dot" style="background:'
        + PROSPECT_COLOR + '">' + (i + 1) + "</span>"
        + "<b>" + esc(p.gnis_name || p.descriptor || ("reach " + p.comid)) + "</b>"
        + '<span class="bl-agent-conf">' + conf + "</span></div>"
        + (p.descriptor ? '<div class="bl-agent-verdict">' + esc(p.descriptor) + "</div>" : "")
        + (ev ? '<ul class="bl-agent-why">' + ev + "</ul>" : "")
        + (p.why_not_higher ? '<div class="bl-agent-note-sm">Why not higher: '
            + esc(p.why_not_higher) + "</div>" : "")
        + (p.needs_access_verify ? '<div class="bl-agent-flag">⚑ access needs verification</div>' : "")
        + "</div>";
    });
    setResults(html);
    wireCardClicks();
  }

  // --------------------------------------------------------------------
  // Small builders
  // --------------------------------------------------------------------
  function pt(lng, lat, props) {
    return { type: "Feature", geometry: { type: "Point", coordinates: [lng, lat] },
             properties: props || {} };
  }
  function fc(features) { return { type: "FeatureCollection", features: features }; }

  function metaBar(res) {
    var lat = res.latency_ms != null ? (res.latency_ms / 1000).toFixed(1) + "s" : "—";
    var cost = res.usage && res.usage.est_cost_usd != null ? "$" + res.usage.est_cost_usd : "—";
    var grounded = res.grounding && res.grounding.ok ? "grounded ✓"
      : "ungrounded: " + ((res.grounding && res.grounding.unsourced) || []).join(", ");
    return '<div class="bl-agent-meta"><span>v' + res.version + " · " + esc(res.orchestrator || "hand")
      + "</span><span>" + lat + "</span><span>" + cost + "</span><span>" + grounded + "</span></div>";
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  // --------------------------------------------------------------------
  // Panel DOM
  // --------------------------------------------------------------------
  function setBusy(b, msg) {
    state.busy = b;
    var el = document.getElementById("bl-agent-status");
    var btns = document.querySelectorAll(".bl-agent-run");
    btns.forEach(function (x) { x.disabled = b; });
    if (el) el.textContent = b ? (msg || "Working…") : "";
    if (el) el.style.display = b ? "block" : "none";
  }
  function showError(e) { setResults('<div class="bl-agent-error">⚠ ' + esc(e.message || e) + "</div>"); }
  function setResults(html) {
    var el = document.getElementById("bl-agent-results");
    if (el) el.innerHTML = html;
  }
  function wireCardClicks() {
    document.querySelectorAll("#bl-agent-results .bl-agent-card").forEach(function (card) {
      card.addEventListener("click", function () {
        var lng = parseFloat(card.getAttribute("data-lng"));
        var lat = parseFloat(card.getAttribute("data-lat"));
        if (!isNaN(lng) && !isNaN(lat) && map()) map().flyTo({ center: [lng, lat], zoom: 12 });
      });
    });
  }

  // Mount into the shared left-rail panel (the same #controls-panel that holds
  // Map Layers / My Content / Map Filters / Map Legend) rather than a separate
  // floating tile. The "Agent" rail + mobile tabs and the empty #bl-agent-root
  // pane ship hidden in index.html; controls.ts already wired their open/close.
  // Here we fill the pane and reveal the tabs.
  function renderPanel(h) {
    injectStyles();
    var root = document.getElementById("bl-agent-root");
    if (!root) return; // panel shell missing (older HTML) -> stay hidden
    root.innerHTML =
        '<div class="bl-agent-models">' + esc(h.models.cheap) + " + " + esc(h.models.strong)
          + (h.has_key ? "" : ' · <span class="bl-agent-flag">no API key set</span>') + "</div>"
        + '<div class="bl-agent-tabs">'
          + '<button class="bl-agent-tab on" data-tab="plan">Plan a trip</button>'
          + '<button class="bl-agent-tab" data-tab="discover">Discover water</button>'
        + "</div>"
        + '<div class="bl-agent-pane" data-pane="plan">'
          + '<input id="bl-agent-prefs" placeholder="Preferences (e.g. dry flies, wadeable)" />'
          // Orchestration toggle: the trip planner ships in TWO interchangeable
          // forms (hand-written loop + LangGraph), so this radio runs that A/B
          // live. The prospector has only a graph build, hence no toggle on the
          // Discover tab (see the note there).
          + '<div class="bl-agent-orch">'
            + '<label><input type="radio" name="bl-agent-orch" value="hand" checked> hand loop</label>'
            + '<label><input type="radio" name="bl-agent-orch" value="graph"> LangGraph</label>'
          + "</div>"
          + '<button class="bl-agent-run" id="bl-agent-plan">Plan from map center</button>'
        + "</div>"
        + '<div class="bl-agent-pane" data-pane="discover" hidden>'
          + '<div class="bl-agent-lede">Find undesignated-but-fishable trout water in the selected state.</div>'
          + '<div class="bl-agent-orch-note">Orchestration: <b>LangGraph</b> — branching + '
            + 'human-in-the-loop. No hand-loop toggle here: the prospector ships only as a '
            + 'graph, so there is no second orchestrator to compare (the planner has both, '
            + 'which is what its toggle exercises).</div>'
          + '<button class="bl-agent-run" id="bl-agent-discover">Discover in this state</button>'
        + "</div>"
        + '<div id="bl-agent-status" class="bl-agent-status" style="display:none"></div>'
        + '<div id="bl-agent-results" class="bl-agent-results"></div>';

    // Reveal the gated nav entries now that health says enabled. Their lucide
    // icon was already hydrated at app boot (the <i> ships in the DOM), but
    // refresh defensively in case boot ran before the CDN script loaded.
    var railTab = document.getElementById("rail-tab-agent");
    if (railTab) railTab.hidden = false;
    var mobileTab = document.getElementById("mobile-tab-agent");
    if (mobileTab) mobileTab.hidden = false;
    if (window.refreshIcons) window.refreshIcons();

    if (h.token_required && !localStorage.getItem(TOKEN_KEY)) {
      var t = window.prompt("Agent demo token (set AGENT_DEMO_TOKEN on the server):");
      if (t) localStorage.setItem(TOKEN_KEY, t);
    }

    root.querySelectorAll(".bl-agent-tab").forEach(function (tab) {
      tab.addEventListener("click", function () {
        root.querySelectorAll(".bl-agent-tab").forEach(function (x) { x.classList.remove("on"); });
        tab.classList.add("on");
        var name = tab.getAttribute("data-tab");
        root.querySelectorAll(".bl-agent-pane").forEach(function (p) {
          p.hidden = p.getAttribute("data-pane") !== name;
        });
      });
    });
    document.getElementById("bl-agent-plan").addEventListener("click", runPlan);
    document.getElementById("bl-agent-discover").addEventListener("click", runDiscover);
  }

  function injectStyles() {
    var css =
      // The panel frame (position, scroll, header/title) now comes from the
      // shared #controls-panel; we only style the agent content mounted into
      // its pane. A little top padding mirrors the other panes' first section.
      "#bl-agent-root{padding-top:14px;font:13.5px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#16202b}"
      + ".bl-agent-models{font-size:11px;color:#647489;margin-bottom:8px}"
      + ".bl-agent-tabs{display:flex;gap:6px;margin-bottom:8px}"
      + ".bl-agent-tab{flex:1;padding:6px;border:1px solid #d8dde4;background:#f4f6f8;border-radius:7px;cursor:pointer;font-size:12px}"
      + ".bl-agent-tab.on{background:#0B2A3A;color:#fff;border-color:#0B2A3A}"
      + "#bl-agent-prefs{width:100%;box-sizing:border-box;padding:7px;border:1px solid #d8dde4;border-radius:7px;margin-bottom:6px}"
      + ".bl-agent-orch{display:flex;gap:12px;font-size:12px;color:#647489;margin-bottom:8px}"
      + ".bl-agent-orch-note{font-size:11px;line-height:1.45;color:#647489;background:#F2EAFB;"
        + "border-left:3px solid #7A3DB8;padding:7px 9px;border-radius:0 7px 7px 0;margin-bottom:8px}"
      + ".bl-agent-orch-note b{color:#5B2C91}"
      + ".bl-agent-run{width:100%;padding:9px;background:#2F6B3D;color:#fff;border:0;border-radius:8px;cursor:pointer;font-weight:600}"
      + ".bl-agent-run:disabled{opacity:.55;cursor:wait}"
      + ".bl-agent-lede{font-size:12px;color:#647489;margin-bottom:8px}"
      + ".bl-agent-status{margin-top:10px;font-size:12px;color:#8A5A14;background:#FBF3E2;padding:8px;border-radius:7px}"
      + ".bl-agent-results{margin-top:10px}"
      + ".bl-agent-meta{display:flex;flex-wrap:wrap;gap:8px;font-size:11px;color:#647489;margin-bottom:8px}"
      + ".bl-agent-card{border:1px solid #e2e6ea;border-radius:9px;padding:8px 10px;margin-bottom:8px;cursor:pointer}"
      + ".bl-agent-card:hover{border-color:#95C5D9;background:#f6fafc}"
      + ".bl-agent-card-h{display:flex;align-items:center;gap:7px}"
      + ".bl-agent-dot{width:18px;height:18px;border-radius:50%;color:#fff;font-size:11px;font-weight:700;"
        + "display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto}"
      + ".bl-agent-card-h b{flex:1;min-width:0}"
      + ".bl-agent-conf{font-size:10px;text-transform:uppercase;color:#647489;letter-spacing:.04em}"
      + ".bl-agent-verdict{font-size:12px;margin:5px 0;color:#2b3744}"
      + ".bl-agent-why{margin:4px 0 0;padding-left:16px;font-size:12px;color:#4a5666}"
      + ".bl-agent-why li{margin:2px 0}"
      + ".bl-agent-note-sm{font-size:11px;color:#8A5A14;margin-top:4px}"
      + ".bl-agent-flag{font-size:11px;color:#8A3327}"
      + ".bl-agent-sub{font-weight:600;margin:6px 0 4px;font-size:12px}"
      + ".bl-agent-blocked{font-size:12px;color:#8A3327;margin:3px 0}"
      + ".bl-agent-notes{font-size:11px;color:#647489;margin-top:8px;font-style:italic}"
      + ".bl-agent-empty{font-size:12px;color:#647489;padding:8px 0}"
      + ".bl-agent-error{font-size:12px;color:#8A3327;background:#F8E7E4;padding:8px;border-radius:7px}";
    var s = document.createElement("style");
    s.textContent = css;
    document.head.appendChild(s);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
