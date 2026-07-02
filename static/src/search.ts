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
import { SEARCH_INDEX_URL, SEARCH_INDEX_ENABLED } from "./config";

// -- Static search index (M4.2) ----------------------------------------
// Gauges + counties + towns, prebuilt by scripts/build_search_index.py
// and fetched ONCE on first search focus (~hundreds of KB gz). Positional
// arrays: gauges [site_no, name, state, lat, lon]; places/counties
// [name, state, lat, lon]. Unset URL => river-catalog-only search (the
// pre-M4.2 behavior).

type GaugeEntry = [string, string, string, number, number];
type PlaceEntry = [string, string, number, number];

interface SearchIndex {
  gauges: GaugeEntry[];
  counties: PlaceEntry[];
  places: PlaceEntry[];
}

let _index: SearchIndex | null = null;
let _indexLoading: Promise<void> | null = null;

function ensureIndex(onReady: () => void): void {
  if (!SEARCH_INDEX_ENABLED || _index || _indexLoading) return;
  _indexLoading = (async () => {
    try {
      let text: string;
      if (SEARCH_INDEX_URL.endsWith(".gz") && "DecompressionStream" in window) {
        const res = await fetch(SEARCH_INDEX_URL);
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        text = await new Response(
          res.body.pipeThrough(new DecompressionStream("gzip")),
        ).text();
      } else {
        const url = SEARCH_INDEX_URL.replace(/\.gz$/, "");
        const res = await fetch(url);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        text = await res.text();
      }
      const d = JSON.parse(text) as Partial<SearchIndex>;
      _index = {
        gauges: d.gauges || [],
        counties: d.counties || [],
        places: d.places || [],
      };
      onReady(); // re-render with the richer pool
    } catch (err) {
      console.warn("search index failed to load:", err);
      _indexLoading = null; // allow a retry on the next focus
    }
  })();
}

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
  // Last-rendered index matches; the result buttons reference them by
  // position (data-j) so click routing needs no re-query.
  let _gaugeMatches: GaugeEntry[] = [];
  let _placeMatches: PlaceEntry[] = [];

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
        .slice(0, 8);
      // Index groups (M4.2): gauges by name or site-number prefix;
      // towns + counties merged under Places.
      _gaugeMatches = _index
        ? _index.gauges
            .filter((g) => g[1].toLowerCase().includes(q) || g[0].startsWith(q))
            .slice(0, 5)
        : [];
      _placeMatches = _index
        ? [..._index.places, ..._index.counties]
            .filter((p) => p[0].toLowerCase().includes(q))
            .slice(0, 5)
        : [];
      if (!matches.length && !_gaugeMatches.length && !_placeMatches.length) {
        const cond = activeConditionFilter();
        const scope = cond
          ? ` with ${condLabel(cond)} conditions`
          : filterOverlayActive()
            ? " matching the current filters"
            : "";
        html = `<div class="search-empty">No matches for "${esc(input!.value.trim())}"${esc(scope)}</div>`;
      } else {
        if (matches.length) {
          html +=
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
        if (_gaugeMatches.length) {
          html +=
            '<div class="search-results-group"><div class="search-results-label">Gauges</div>' +
            _gaugeMatches
              .map(
                (g, j) =>
                  `<button type="button" class="search-result" data-kind="gauge" data-j="${j}">` +
                  `<span class="search-result-icon"><i data-lucide="droplets"></i></span>` +
                  `<span class="search-result-text"><span class="search-result-name">${hlName(g[1], q)}</span>` +
                  `<span class="search-result-meta">USGS ${esc(g[0])} &middot; ${esc(g[2])}</span></span></button>`,
              )
              .join("") +
            "</div>";
        }
        if (_placeMatches.length) {
          html +=
            '<div class="search-results-group"><div class="search-results-label">Places</div>' +
            _placeMatches
              .map(
                (p, j) =>
                  `<button type="button" class="search-result" data-kind="place" data-j="${j}">` +
                  `<span class="search-result-icon"><i data-lucide="map-pin"></i></span>` +
                  `<span class="search-result-text"><span class="search-result-name">${hlName(p[0], q)}</span>` +
                  `<span class="search-result-meta">${esc(p[1])}</span></span></button>`,
              )
              .join("") +
            "</div>";
        }
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
        if (b.dataset.kind === "gauge") {
          const g = _gaugeMatches[Number(b.dataset.j)];
          if (g) selectGauge(g);
          return;
        }
        if (b.dataset.kind === "place") {
          const p = _placeMatches[Number(b.dataset.j)];
          if (p) selectPlace(p);
          return;
        }
        const i = Number(b.dataset.i);
        const r = rivers()[i];
        if (r) selectResult(r);
      }),
    );
  }

  /** Close the dropdown + reset the pill (shared by all select paths). */
  function closeSearch(): void {
    focused = false;
    pill!.classList.remove("is-focused");
    results!.hidden = true;
    input!.value = "";
    input!.blur();
    applyCollapsedState();
  }

  /** A gauge in the live catalog opens its river panel; otherwise fly
   *  to the site (viewport mode will pick its rivers up on settle). */
  function selectGauge(g: GaugeEntry): void {
    const river = rivers().find((r) =>
      r.site_no === g[0] || (r.gauges || []).some((x) => x.site_no === g[0]),
    );
    map.flyTo({ center: [g[4], g[3]], zoom: Math.max(map.getZoom(), 12) });
    if (river) selectRiver(river);
    closeSearch();
  }

  function selectPlace(p: PlaceEntry): void {
    const county = p[0].toLowerCase().includes("county");
    map.flyTo({ center: [p[3], p[2]], zoom: county ? 9.5 : 11.5 });
    closeSearch();
  }

  function selectResult(r: River): void {
    pushRecent(r);
    map.flyTo({ center: riverLngLat(r), zoom: Math.max(map.getZoom(), 12) });
    // Central selection: opens the panel + highlights the reaches.
    selectRiver(r);
    closeSearch();
  }

  function openSearch(): void {
    applyCollapsedState();
    focused = true;
    pill!.classList.add("is-focused");
    setTimeout(() => input!.focus(), 0);
    ensureIndex(renderResults); // lazy one-time index fetch (M4.2)
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
    ensureIndex(renderResults);
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
