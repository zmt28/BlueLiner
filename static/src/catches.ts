/**
 * Catch log: the form to record a catch, the My Catches list panel,
 * the per-catch enrichment-preview that fetches conditions for the
 * lat/lon at the moment the user is logging. Plus the two river-
 * panel-body wirers (wireCatch + wireTrend) that openRiverPanel calls
 * after injecting the panel HTML -- they're tightly coupled to the
 * catch CTA / gauge-trend buttons rendered inside that body, so they
 * live here rather than in river-panel.ts.
 *
 * Extracted from app.js in PR B1j (the last extraction).
 *
 * Owns:
 *   - SPECIES list (the datalist of common species)
 *   - catchCtx (the river-context held between form open and submit)
 *   - openCatchForm(river): pre-fills the form + opens the modal +
 *     kicks off loadEnrichmentPreview
 *   - loadEnrichmentPreview: /api/catches/enrichment-preview fetch
 *   - renderEnv: the chip strip that summarizes the captured
 *     conditions inside the form
 *   - submitCatch: POST /api/catches with the form payload
 *   - openMyCatches + renderCatchList + deleteCatch: the My Catches
 *     panel
 *   - wireCatchUI: form submit + datetime onchange + back-button
 *     handlers (run at module init)
 *   - wireCatch(root, river): injects the "Log a catch" CTA into a
 *     river-panel body, dispatching on getCurrentUser() to either
 *     open the catch form or nudge the user to sign in. Exposed via
 *     window so the legacy river-panel.ts call site keeps working.
 *   - wireTrend(root): wires gauge-trend on-demand sparkline
 *     buttons inside the river-panel body. Exposed via window for
 *     the same reason.
 *
 * Cross-module deps:
 *   - esc from util
 *   - sparkline + wireSparkHover from sparkline (used by wireTrend)
 *   - getCurrentUser + openModal + closeModal from auth (CATCH CTA
 *     dispatch + modal primitives)
 */

import { esc } from "./util";
import { sparkline, wireSparkHover } from "./sparkline";
import { getCurrentUser, openModal, closeModal } from "./auth";

// -- Species datalist (filled into #cf-species-list on first open) -

const SPECIES = [
  "Brown Trout",
  "Rainbow Trout",
  "Brook Trout",
  "Cutthroat Trout",
  "Tiger Trout",
  "Smallmouth Bass",
  "Largemouth Bass",
  "Bluegill",
  "Carp",
  "Fallfish",
  "Chain Pickerel",
  "Walleye",
];

// -- Catch form state ----------------------------------------------
// Context for the form: which river it was launched from. Drives the
// enrichment lat/lon/site_no even if the user edits the river name.

interface CatchCtx {
  river_name: string;
  river_site_no: string | null;
  lat: number | null;
  lon: number | null;
}

let catchCtx: CatchCtx | null = null;

function _toLocalInputValue(d: Date): string {
  // datetime-local wants "YYYY-MM-DDTHH:MM" in *local* time.
  const off = d.getTimezoneOffset();
  const local = new Date(d.getTime() - off * 60000);
  return local.toISOString().slice(0, 16);
}

interface RiverLike {
  name?: string | null;
  site_no?: string | null;
  lat?: number | null;
  lon?: number | null;
}

export function openCatchForm(river: RiverLike): void {
  catchCtx = {
    river_name: river.name || "",
    river_site_no: river.site_no || null,
    lat: river.lat ?? null,
    lon: river.lon ?? null,
  };
  // Populate species datalist once.
  const dl = document.getElementById("cf-species-list");
  if (dl && !(dl as HTMLElement).dataset.filled) {
    (dl as HTMLElement).dataset.filled = "1";
    for (const s of SPECIES) {
      const o = document.createElement("option");
      o.value = s;
      dl.appendChild(o);
    }
  }
  (document.getElementById("catch-form") as HTMLFormElement).reset();
  (document.getElementById("cf-river") as HTMLInputElement).value = catchCtx.river_name;
  (document.getElementById("cf-when") as HTMLInputElement).value = _toLocalInputValue(new Date());
  (document.getElementById("cf-error") as HTMLElement).textContent = "";
  openModal("catch-modal");
  loadEnrichmentPreview();
}

async function loadEnrichmentPreview(): Promise<void> {
  const body = document.getElementById("cf-enrich-body") as HTMLElement;
  body.innerHTML = '<div class="cf-enrich-loading">Reading current conditions&hellip;</div>';
  if (!catchCtx || catchCtx.lat == null || catchCtx.lon == null) {
    body.innerHTML =
      '<div class="cf-enrich-loading">No location — conditions won’t be captured.</div>';
    return;
  }
  const p = new URLSearchParams({
    lat: String(catchCtx.lat),
    lon: String(catchCtx.lon),
  });
  if (catchCtx.river_site_no) p.set("site_no", catchCtx.river_site_no);
  if (catchCtx.river_name) p.set("river_name", catchCtx.river_name);
  const when = (document.getElementById("cf-when") as HTMLInputElement).value;
  if (when) p.set("occurred_at", new Date(when).toISOString());
  try {
    const env: CatchEnrichment = await fetch(
      `/api/catches/enrichment-preview?${p}`,
    ).then((r) => r.json());
    body.innerHTML = renderEnv(env);
  } catch {
    body.innerHTML =
      '<div class="cf-enrich-loading">Conditions unavailable right now.</div>';
  }
}

function renderEnv(env: CatchEnrichment | null): string {
  if (!env) return '<div class="cf-enrich-loading">No conditions.</div>';
  const rows: Array<[string, string, string]> = [];
  const flow =
    env.flow_cfs != null
      ? `${env.flow_cfs} cfs${
          (env as { flow_vs_median?: string }).flow_vs_median
            ? " (" + esc((env as { flow_vs_median?: string }).flow_vs_median) + ")"
            : ""
        }`
      : null;
  if (flow) rows.push(["💧", "Flow", flow]);
  if (env.water_temp_f != null)
    rows.push(["🌡", "Water", `${env.water_temp_f}°F`]);
  if (env.air_temp_f != null) {
    rows.push([
      "☁",
      "Air",
      `${env.air_temp_f}°F${env.conditions ? ", " + esc(env.conditions) : ""}`,
    ]);
  }
  const pressure = (env as { pressure_inhg?: number }).pressure_inhg;
  if (pressure != null) rows.push(["📊", "Pressure", `${pressure} inHg`]);
  const moon = (env as { moon_phase?: string }).moon_phase;
  if (moon) rows.push(["🌙", "Moon", esc(moon)]);
  const hatches = (env as { active_hatches?: string[] }).active_hatches;
  if (hatches && hatches.length) {
    rows.push(["🦟", "Hatches", hatches.map(esc).join(", ")]);
  }
  if (!rows.length)
    return '<div class="cf-enrich-loading">No conditions captured for this spot.</div>';
  return rows
    .map(
      (r) =>
        `<div class="cf-env-row"><span class="cf-env-ic">${r[0]}</span>` +
        `<span class="cf-env-k">${r[1]}</span><span class="cf-env-v">${r[2]}</span></div>`,
    )
    .join("");
}

async function submitCatch(ev: SubmitEvent): Promise<void> {
  ev.preventDefault();
  const species = (document.getElementById("cf-species") as HTMLInputElement).value.trim();
  const err = document.getElementById("cf-error") as HTMLElement;
  if (!species) {
    err.textContent = "Species is required.";
    return;
  }
  const lenRaw = (document.getElementById("cf-length") as HTMLInputElement).value;
  const whenRaw = (document.getElementById("cf-when") as HTMLInputElement).value;
  const payload = {
    species,
    river_name:
      (document.getElementById("cf-river") as HTMLInputElement).value.trim() || null,
    river_site_no: catchCtx ? catchCtx.river_site_no : null,
    lat: catchCtx ? catchCtx.lat : null,
    lon: catchCtx ? catchCtx.lon : null,
    length_in: lenRaw ? parseFloat(lenRaw) : null,
    fly_used:
      (document.getElementById("cf-fly") as HTMLInputElement).value.trim() || null,
    notes:
      (document.getElementById("cf-notes") as HTMLTextAreaElement).value.trim() ||
      null,
    occurred_at: whenRaw ? new Date(whenRaw).toISOString() : null,
  };
  const btn = document.getElementById("cf-save") as HTMLButtonElement;
  btn.disabled = true;
  btn.textContent = "Saving…";
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
  btn.disabled = false;
  btn.textContent = "Save catch";
}

// -- My Catches panel ----------------------------------------------

export async function openMyCatches(): Promise<void> {
  const panel = document.getElementById("catches-panel") as HTMLElement;
  panel.hidden = false;
  const list = document.getElementById("catches-list") as HTMLElement;
  list.innerHTML = '<div class="catches-empty">Loading…</div>';
  try {
    const data = (await fetch("/api/catches").then((r) => r.json())) as {
      total?: number;
      catches?: Catch[];
    };
    (document.getElementById("catches-count") as HTMLElement).textContent =
      data.total
        ? `${data.total} catch${data.total === 1 ? "" : "es"}`
        : "";
    renderCatchList(data.catches || []);
  } catch {
    list.innerHTML =
      '<div class="catches-empty">Could not load your catches.</div>';
  }
}

function renderCatchList(catches: Catch[]): void {
  const list = document.getElementById("catches-list") as HTMLElement;
  if (!catches.length) {
    list.innerHTML =
      '<div class="catches-empty"><div class="catches-empty-ic">🎣</div>' +
      "<p>No catches logged yet.</p>" +
      '<p class="modal-fine">Tap any river on the map and hit ' +
      "“Log a catch” — we’ll capture the conditions automatically.</p></div>";
    return;
  }
  list.innerHTML = "";
  for (const c of catches) {
    const when = c.occurred_at ? new Date(c.occurred_at) : null;
    const dateStr = when
      ? when.toLocaleDateString(undefined, { month: "short", day: "numeric" })
      : "";
    const env = (c.env || {}) as CatchEnv;
    const envChips = (
      [
        env.flow_cfs != null ? `💧${env.flow_cfs}cfs` : null,
        env.water_temp_f != null ? `🌡${env.water_temp_f}°F` : null,
        env.air_temp_f != null ? `☁${env.air_temp_f}°F` : null,
      ].filter(Boolean) as string[]
    )
      .map(esc)
      .join("  ");
    const sub = (
      [
        c.species,
        c.length_in != null ? `${c.length_in}"` : null,
        c.fly_used,
      ].filter(Boolean) as string[]
    )
      .map(esc)
      .join(" · ");
    const row = document.createElement("div");
    row.className = "catch-row";
    row.innerHTML =
      `<div class="catch-row-head"><span class="catch-date">${esc(dateStr)}</span>` +
      `<span class="catch-river">${esc(c.river_name || "Unknown water")}</span></div>` +
      `<div class="catch-sub">${sub}</div>` +
      (envChips ? `<div class="catch-env">${envChips}</div>` : "") +
      (c.notes ? `<div class="catch-notes">${esc(c.notes)}</div>` : "") +
      `<button class="catch-del" data-id="${c.id}">Delete</button>`;
    (row.querySelector(".catch-del") as HTMLButtonElement).onclick = () =>
      deleteCatch(c.id, row);
    list.appendChild(row);
  }
}

async function deleteCatch(id: number, rowEl: HTMLElement): Promise<void> {
  if (!confirm("Delete this catch?")) return;
  try {
    const r = await fetch(`/api/catches/${id}`, { method: "DELETE" });
    if (r.ok || r.status === 204) {
      rowEl.remove();
      const list = document.getElementById("catches-list") as HTMLElement;
      if (!list.children.length) renderCatchList([]);
    }
  } catch {
    /* ignore */
  }
}

// -- Module-init DOM wiring ----------------------------------------

function wireCatchUI(): void {
  const form = document.getElementById("catch-form");
  if (form)
    form.addEventListener("submit", (e) =>
      submitCatch(e as SubmitEvent),
    );
  const whenInput = document.getElementById("cf-when");
  if (whenInput)
    whenInput.addEventListener("change", () => loadEnrichmentPreview());
  const back = document.getElementById("catches-back");
  if (back)
    back.addEventListener("click", () => {
      (document.getElementById("catches-panel") as HTMLElement).hidden = true;
    });
}

wireCatchUI();

// -- River-panel-body wirers (called by openRiverPanel) ------------
// Both are reached from river-panel.ts via window.wireTrend /
// window.wireCatch (it doesn't import this module to avoid a cycle;
// rivers/streams/etc. all transitively depend on river-panel, and
// river-panel depending on catches would close the loop).

/** Wire each gauge's on-demand "show flow trend" button within
 *  `root` (the river-detail-panel body). The primary gauge's chart
 *  is loaded eagerly elsewhere; this covers secondary gauges. */
export function wireTrend(root: HTMLElement | null): void {
  if (!root) return;
  root.querySelectorAll<HTMLButtonElement>(".bl-trend-btn").forEach((btn) => {
    if (btn.dataset.wired) return;
    btn.dataset.wired = "1";
    const site = btn.getAttribute("data-site");
    const box = root.querySelector<HTMLElement>(
      `.bl-trend[data-site="${site}"]`,
    );
    btn.onclick = async () => {
      btn.disabled = true;
      if (box)
        box.innerHTML =
          '<div class="bl-trend-msg">Loading 1-yr trend&hellip;</div>';
      try {
        const d: HistoryResponse = await fetch(
          `/api/history?site_no=${encodeURIComponent(site || "")}`,
        ).then((r) => r.json());
        if (box) {
          box.innerHTML = sparkline(d.series);
          wireSparkHover(box);
        }
      } catch (_) {
        if (box)
          box.innerHTML =
            '<div class="bl-trend-msg">Trend unavailable.</div>';
      }
      btn.style.display = "none";
    };
  });
}

/** Inject the "Log a catch" CTA into the panel `root`, wired to
 *  `river`. Signed-out users get a sign-in nudge instead. */
export function wireCatch(
  root: HTMLElement | null,
  river: RiverLike | null,
): void {
  if (!root || !river) return;
  let slot = root.querySelector<HTMLElement>(".bl-catch-cta");
  if (!slot) {
    // Older cached popup HTML without the placeholder: append one.
    slot = document.createElement("div");
    slot.className = "bl-catch-cta";
    root.appendChild(slot);
  }
  if (slot.dataset.wired) return;
  slot.dataset.wired = "1";

  if (getCurrentUser()) {
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

// -- Window bridges ------------------------------------------------
// river-panel.ts and auth.ts (for "my-catches" account action) reach
// these via window to avoid import cycles.

declare global {
  interface Window {
    wireTrend: typeof wireTrend;
    wireCatch: typeof wireCatch;
    openMyCatches: typeof openMyCatches;
    openCatchForm: typeof openCatchForm;
  }
}

window.wireTrend = wireTrend;
window.wireCatch = wireCatch;
window.openMyCatches = openMyCatches;
window.openCatchForm = openCatchForm;
