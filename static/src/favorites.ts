/**
 * Favorite waters (M4.1): the bookmark toggle on the river panel, the
 * Favorites list in My Content, and the client side of condition
 * alerts (the emails themselves are diffed + sent by the precompute
 * pass server-side).
 *
 * Favorites are account-tied (alerts need an address): the bookmark
 * opens the login modal when signed out. Toggles are OPTIMISTIC — the
 * UI commits immediately and rolls back with an error toast if the
 * write fails (the pin-flow pattern).
 *
 * Cross-module deps: auth (getCurrentUser/openModal), map-setup (map),
 * toast, util. Selecting a favorited river reuses window.selectRiver
 * (selection.ts; window bridge because selection -> river-panel ->
 * this module would otherwise cycle).
 */

import { map } from "./map-setup";
import { getCurrentUser, openModal } from "./auth";
import { showToast } from "./toast";
import { esc, refreshIcons } from "./util";
import { getCurrentSt } from "./state";

interface Favorite {
  site_no: string;
  name: string;
  state: string;
  lat: number | null;
  lon: number | null;
  notify: boolean;
  last_overall: string | null;
  created_at: string;
}

const _favs = new Map<string, Favorite>();

export function isFavorite(siteNo: string | null | undefined): boolean {
  return !!siteNo && _favs.has(siteNo);
}

/** Fetch the signed-in user's favorites (clears when signed out). */
export async function loadFavorites(): Promise<void> {
  if (!getCurrentUser()) {
    _favs.clear();
    renderFavoritesList();
    return;
  }
  try {
    const r = await fetch("/api/favorites");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = (await r.json()) as { favorites?: Favorite[] };
    _favs.clear();
    for (const f of d.favorites || []) _favs.set(f.site_no, f);
  } catch (err) {
    console.warn("favorites failed to load:", err);
  }
  renderFavoritesList();
}

// -- Panel bookmark button ---------------------------------------------

function setBtnState(btn: HTMLButtonElement, on: boolean): void {
  btn.classList.toggle("is-fav", on);
  btn.title = on
    ? "Remove from favorites"
    : "Add to favorites (get condition alerts)";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
}

async function toggleFavorite(
  river: RiverLikeFav,
  btn: HTMLButtonElement | null,
): Promise<void> {
  if (!getCurrentUser()) {
    openModal("login-modal");
    return;
  }
  const site = river.site_no;
  if (!site) return;
  const adding = !_favs.has(site);
  const prev = _favs.get(site);
  // Optimistic commit (rolled back below on failure).
  if (adding) {
    _favs.set(site, {
      site_no: site,
      name: river.name || site,
      state: getCurrentSt() || "",
      lat: river.lat ?? null,
      lon: river.lon ?? null,
      notify: true,
      last_overall: null,
      created_at: "",
    });
  } else {
    _favs.delete(site);
  }
  if (btn) setBtnState(btn, adding);
  renderFavoritesList();
  try {
    const res = adding
      ? await fetch("/api/favorites", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            site_no: site,
            name: river.name || site,
            state: getCurrentSt() || "",
            lat: river.lat ?? null,
            lon: river.lon ?? null,
          }),
        })
      : await fetch(`/api/favorites/${encodeURIComponent(site)}`, {
          method: "DELETE",
        });
    if (!res.ok && !(res.status === 404 && !adding)) {
      throw new Error(`HTTP ${res.status}`);
    }
    if (adding) {
      _favs.set(site, (await res.json()) as Favorite);
      renderFavoritesList();
      showToast("Saved — you'll get alerts when conditions change", "success");
    }
  } catch {
    // Roll back the optimistic commit.
    if (adding) _favs.delete(site);
    else if (prev) _favs.set(site, prev);
    if (btn) setBtnState(btn, !adding);
    renderFavoritesList();
    showToast("Couldn't update favorites — try again.", "error");
  }
}

interface RiverLikeFav {
  name?: string | null;
  site_no?: string | null;
  lat?: number | null;
  lon?: number | null;
}

/** Inject the bookmark toggle into a freshly-opened river panel.
 *  Gauged rivers only — alerts diff gauge verdicts, so an ungauged
 *  reach has nothing to alert on. */
export function wireFavorite(body: HTMLElement, river: RiverLikeFav): void {
  if (!river.site_no) return;
  const row =
    body.querySelector(".panel-title-row") || body.querySelector(".bl-card-head");
  if (!row || row.querySelector(".bl-fav-btn")) return;
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "bl-fav-btn";
  btn.innerHTML = '<i data-lucide="bookmark" aria-hidden="true"></i>';
  setBtnState(btn, isFavorite(river.site_no));
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    void toggleFavorite(river, btn);
  });
  row.appendChild(btn);
  refreshIcons();
}

// -- My Content list -----------------------------------------------------

const VERDICT_CLASS: Record<string, string> = {
  green: "good",
  yellow: "fair",
  red: "poor",
};

function renderFavoritesList(): void {
  const box = document.getElementById("fav-list");
  if (!box) return;
  if (!getCurrentUser()) {
    box.innerHTML =
      '<button type="button" class="fav-empty fav-signin">' +
      "Sign in to save favorite waters</button>";
    box.querySelector(".fav-signin")?.addEventListener("click", () =>
      openModal("login-modal"),
    );
    return;
  }
  if (!_favs.size) {
    box.innerHTML =
      '<div class="fav-empty">Tap the bookmark on a river panel to save ' +
      "it here — you'll get an email when conditions flip.</div>";
    return;
  }
  box.innerHTML = [..._favs.values()]
    .map((f) => {
      const dot = VERDICT_CLASS[f.last_overall || ""] || "none";
      return (
        `<div class="fav-row" data-site="${esc(f.site_no)}">` +
        `<span class="fav-dot is-${dot}"></span>` +
        `<button type="button" class="fav-name">${esc(f.name)}` +
        `<span class="fav-state">${esc(f.state)}</span></button>` +
        `<button type="button" class="fav-bell${f.notify ? " on" : ""}" ` +
        `title="${f.notify ? "Alerts on" : "Alerts off"}">` +
        `<i data-lucide="${f.notify ? "bell" : "bell-off"}" aria-hidden="true"></i>` +
        `</button>` +
        `<button type="button" class="fav-remove" title="Remove">&times;</button>` +
        `</div>`
      );
    })
    .join("");
  refreshIcons();
  for (const row of box.querySelectorAll<HTMLElement>(".fav-row")) {
    const site = row.dataset.site || "";
    const fav = _favs.get(site);
    if (!fav) continue;
    (row.querySelector(".fav-name") as HTMLButtonElement).onclick = () =>
      openFavorite(fav);
    (row.querySelector(".fav-bell") as HTMLButtonElement).onclick = () =>
      void toggleNotify(fav);
    (row.querySelector(".fav-remove") as HTMLButtonElement).onclick = () =>
      void toggleFavorite(fav, null);
  }
}

/** Fly to a favorite and (when it's in the live catalog) select it. */
function openFavorite(f: Favorite): void {
  document.dispatchEvent(new Event("bl:poi-open")); // close the rail panel
  if (f.lat != null && f.lon != null) {
    map.flyTo({ center: [f.lon, f.lat], zoom: Math.max(map.getZoom(), 11) });
  }
  const river = (window.allRivers || []).find((r) => r.site_no === f.site_no);
  if (river && window.selectRiver) window.selectRiver(river);
}

async function toggleNotify(f: Favorite): Promise<void> {
  const next = !f.notify;
  f.notify = next; // optimistic
  renderFavoritesList();
  try {
    const r = await fetch(`/api/favorites/${encodeURIComponent(f.site_no)}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ notify: next }),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
  } catch {
    f.notify = !next; // rollback
    renderFavoritesList();
    showToast("Couldn't update alerts — try again.", "error");
  }
}

// -- Window bridge -------------------------------------------------------

declare global {
  interface Window {
    loadFavorites: typeof loadFavorites;
  }
}

window.loadFavorites = loadFavorites;
