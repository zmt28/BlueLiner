/**
 * Floating river search. Wired to the live river catalog (window.allRivers,
 * kept fresh by rivers.ts as the user pans / switches state). Typing filters
 * by river name; selecting a result pans the map to the river and opens the
 * existing river-detail panel.
 *
 * Desktop: an always-expanded pill in the top-left of the map.
 * Mobile (< 760px): starts as a round icon button that expands into the pill
 * on tap and re-collapses on outside-tap when the query is empty.
 * Cmd/Ctrl-K focuses the search from anywhere.
 *
 * Cross-module deps:
 *   - map from map-setup (pan to a result)
 *   - selectRiver from selection (open the detail panel + highlight on select)
 *   - refreshIcons / esc from util
 */

import { map } from "./map-setup";
import { selectRiver } from "./selection";
import { activeConditionFilter, filterOverlayActive } from "./streams";
import { riverPasses } from "./rivers";
import { riverLngLat } from "./coords";
import { refreshIcons, esc } from "./util";

const MOBILE_BP = "(max-width: 759px)";
const COND_VARIANT: Record<string, "good" | "fair" | "poor" | "none"> = {
  green: "good",
  yellow: "fair",
  red: "poor",
  gray: "none",
};

const wrap = document.getElementById("search-wrap") as HTMLElement | null;
const iconBtn = document.getElementById("search-icon-btn") as HTMLButtonElement | null;
const pill = document.getElementById("search-pill") as HTMLElement | null;
const input = document.getElementById("search-input") as HTMLInputElement | null;
const results = document.getElementById("search-results") as HTMLElement | null;

function isMobileView(): boolean {
  return window.matchMedia(MOBILE_BP).matches;
}

if (wrap && iconBtn && pill && input && results) {
  let focused = false;

  function rivers(): River[] {
    return (window.allRivers as River[] | undefined) || [];
  }

  /** The searchable pool: the live catalog, scoped by the Filters pane
   *  (the filter overlay, streams.ts) when any control is active -- the
   *  map fades non-matching rivers, so surfacing them here would
   *  contradict the overlay. riverPasses covers condition + stocked +
   *  hatch in one predicate. */
  function pool(): River[] {
    const all = rivers();
    if (!filterOverlayActive()) return all;
    return all.filter(riverPasses);
  }

  /** Collapse to the round icon button (mobile only, empty query). */
  function applyCollapsedState(): void {
    if (isMobileView() && !focused && !input!.value.trim()) {
      pill!.hidden = true;
      iconBtn!.hidden = false;
    } else {
      iconBtn!.hidden = true;
      pill!.hidden = false;
    }
  }

  function condClass(overall: string): string {
    return COND_VARIANT[overall] || "none";
  }
  function condLabel(overall: string): string {
    const v = condClass(overall);
    return v === "none" ? "No data" : v;
  }

  // -- Recents (M2.f3) -- real recently-selected rivers, localStorage.
  // The empty-query list used to show the first 5 catalog rivers under a
  // clock icon, which read as recents but wasn't.
  const RECENTS_KEY = "bl_recent_rivers";

  function loadRecents(): { site_no: string | null; name: string }[] {
    try {
      return JSON.parse(localStorage.getItem(RECENTS_KEY) || "[]") as {
        site_no: string | null;
        name: string;
      }[];
    } catch {
      return [];
    }
  }

  function pushRecent(r: River): void {
    try {
      const cur = loadRecents().filter((x) => x.name !== r.name);
      cur.unshift({ site_no: r.site_no || null, name: r.name });
      localStorage.setItem(RECENTS_KEY, JSON.stringify(cur.slice(0, 6)));
    } catch {
      /* localStorage unavailable */
    }
  }

  /** Escape + wrap the matched substring in <mark> (M2.f2). */
  function hlName(name: string, q: string): string {
    if (!q) return esc(name);
    const i = name.toLowerCase().indexOf(q);
    if (i < 0) return esc(name);
    return (
      esc(name.slice(0, i)) +
      "<mark>" +
      esc(name.slice(i, i + q.length)) +
      "</mark>" +
      esc(name.slice(i + q.length))
    );
  }

  // -- Keyboard navigation (M2.f1) --------------------------------------
  let activeIdx = -1;

  function resultButtons(): HTMLButtonElement[] {
    return [...results!.querySelectorAll<HTMLButtonElement>(".search-result")];
  }

  function setActive(idx: number): void {
    const items = resultButtons();
    if (!items.length) return;
    activeIdx = ((idx % items.length) + items.length) % items.length;
    items.forEach((el, i) => el.classList.toggle("is-active", i === activeIdx));
    items[activeIdx].scrollIntoView({ block: "nearest" });
  }

  function renderResults(): void {
    const q = input!.value.trim().toLowerCase();
    const all = rivers();
    const scoped = pool(); // condition-scoped; === all when filter is Any
    if (!focused) {
      results!.hidden = true;
      return;
    }
    let html = "";
    if (!q) {
      // Real recents when we have them (matched into the current pool);
      // otherwise a plain "Rivers" sampler -- no clock icon pretending.
      const rec = loadRecents()
        .map((x) =>
          scoped.find(
            (r) => (x.site_no && r.site_no === x.site_no) || r.name === x.name,
          ),
        )
        .filter((r): r is River => !!r)
        .slice(0, 5);
      const list = rec.length ? rec : scoped.slice(0, 5);
      const groupLabel = rec.length ? "Recent" : "Rivers";
      const icon = rec.length ? "clock" : "waves";
      if (list.length) {
        html =
          `<div class="search-results-group"><div class="search-results-label">${groupLabel}</div>` +
          list
            .map(
              (r) =>
                `<button type="button" class="search-result" data-i="${all.indexOf(r)}">` +
                `<span class="search-result-icon"><i data-lucide="${icon}"></i></span>` +
                `<span class="search-result-text"><span class="search-result-name">${esc(r.name)}</span>` +
                `<span class="search-result-meta">${esc(r.label || "")}</span></span></button>`,
            )
            .join("") +
          "</div>";
      }
    } else {
      const matches = scoped
        .filter((r) => r.name.toLowerCase().includes(q))
        .slice(0, 12);
      if (!matches.length) {
        const cond = activeConditionFilter();
        const scope = cond
          ? ` with ${condLabel(cond)} conditions`
          : filterOverlayActive()
            ? " matching the current filters"
            : "";
        html = `<div class="search-empty">No matches for "${esc(input!.value.trim())}"${esc(scope)}</div>`;
      } else {
        html =
          '<div class="search-results-group"><div class="search-results-label">Rivers</div>' +
          matches
            .map((r) => {
              const idx = all.indexOf(r);
              const cond = condClass(r.conditions?.overall || "gray");
              return (
                `<button type="button" class="search-result" data-i="${idx}">` +
                `<span class="search-result-icon"><i data-lucide="waves"></i></span>` +
                `<span class="search-result-text"><span class="search-result-name">${hlName(r.name, q)}</span>` +
                `<span class="search-result-meta">${esc(r.label || "")}</span></span>` +
                `<span class="search-result-cond is-${cond}">${condLabel(r.conditions?.overall || "gray")}</span>` +
                `</button>`
              );
            })
            .join("") +
          "</div>";
      }
    }
    if (!html) {
      results!.hidden = true;
      return;
    }
    results!.innerHTML = html;
    results!.hidden = false;
    activeIdx = -1; // fresh list -> no keyboard selection yet
    refreshIcons();
    results!.querySelectorAll<HTMLButtonElement>(".search-result").forEach((b) =>
      b.addEventListener("click", () => {
        const i = Number(b.dataset.i);
        const r = rivers()[i];
        if (r) selectResult(r);
      }),
    );
  }

  function selectResult(r: River): void {
    pushRecent(r);
    map.flyTo({ center: riverLngLat(r), zoom: Math.max(map.getZoom(), 12) });
    // Central selection: opens the panel + highlights the reaches.
    selectRiver(r);
    focused = false;
    pill!.classList.remove("is-focused");
    results!.hidden = true;
    input!.value = "";
    input!.blur();
    applyCollapsedState();
  }

  function openSearch(): void {
    applyCollapsedState();
    focused = true;
    pill!.classList.add("is-focused");
    setTimeout(() => input!.focus(), 0);
    renderResults();
  }

  iconBtn.addEventListener("click", openSearch);
  pill.addEventListener("click", () => {
    if (!focused) {
      focused = true;
      pill!.classList.add("is-focused");
      renderResults();
    }
  });
  input.addEventListener("focus", () => {
    focused = true;
    pill!.classList.add("is-focused");
    renderResults();
  });
  input.addEventListener("input", renderResults);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      input!.value = "";
      focused = false;
      pill!.classList.remove("is-focused");
      results!.hidden = true;
      input!.blur();
      applyCollapsedState();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive(activeIdx + 1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive(activeIdx - 1);
    } else if (e.key === "Enter") {
      const target =
        results!.querySelector<HTMLButtonElement>(".search-result.is-active") ||
        results!.querySelector<HTMLButtonElement>(".search-result");
      if (target) target.click();
    }
  });

  // Outside click: blur + (mobile, empty) collapse.
  document.addEventListener("click", (e) => {
    const t = e.target as Node | null;
    if (t && wrap.contains(t)) return;
    focused = false;
    pill!.classList.remove("is-focused");
    results!.hidden = true;
    applyCollapsedState();
  });

  // Cmd/Ctrl-K focuses search from anywhere.
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      openSearch();
    }
  });

  window.addEventListener("resize", applyCollapsedState);
  applyCollapsedState();
}
