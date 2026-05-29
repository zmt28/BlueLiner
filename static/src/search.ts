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
 *   - openRiverPanel from river-panel (open the detail panel on select)
 *   - refreshIcons / esc from util
 */

import { map } from "./map-setup";
import { openRiverPanel } from "./river-panel";
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

  function renderResults(): void {
    const q = input!.value.trim().toLowerCase();
    const all = rivers();
    if (!focused) {
      results!.hidden = true;
      return;
    }
    let html = "";
    if (!q) {
      const recent = all.slice(0, 5);
      if (recent.length) {
        html =
          '<div class="search-results-group"><div class="search-results-label">Rivers</div>' +
          recent
            .map(
              (r, i) =>
                `<button type="button" class="search-result" data-i="${i}">` +
                `<span class="search-result-icon"><i data-lucide="clock"></i></span>` +
                `<span class="search-result-text"><span class="search-result-name">${esc(r.name)}</span>` +
                `<span class="search-result-meta">${esc(r.label || "")}</span></span></button>`,
            )
            .join("") +
          "</div>";
      }
    } else {
      const matches = all
        .filter((r) => r.name.toLowerCase().includes(q))
        .slice(0, 12);
      if (!matches.length) {
        html = `<div class="search-empty">No matches for "${esc(input!.value.trim())}"</div>`;
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
                `<span class="search-result-text"><span class="search-result-name">${esc(r.name)}</span>` +
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
    refreshIcons();
    results!.querySelectorAll<HTMLButtonElement>(".search-result").forEach((b) =>
      b.addEventListener("click", () => {
        const i = Number(b.dataset.i);
        const r = rivers()[i];
        if (r) selectRiver(r);
      }),
    );
  }

  function selectRiver(r: River): void {
    map.flyTo({ center: riverLngLat(r), zoom: Math.max(map.getZoom(), 12) });
    openRiverPanel(r, null);
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
    } else if (e.key === "Enter") {
      const first = results!.querySelector<HTMLButtonElement>(".search-result");
      if (first) first.click();
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
