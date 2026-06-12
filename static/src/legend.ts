/**
 * Legend pane content, generated from the same exports the map renders
 * with -- tier rows from TIER_COLOR / TIER_LABEL (streams.ts), point
 * rows from poi-icons.ts -- so the legend cannot drift from the map
 * (the old HTML hand-copied the tier hexes). Static legend sections
 * (gauge conditions, public lands, the scoring note) stay in
 * index.html; this module fills the #legend-tiers / #legend-points
 * placeholders and stamps the matching POI glyph discs into the
 * Filters-pane layer rows ([data-poi]).
 *
 * Runs at import (app-boot loads after the DOM is parsed, same pattern
 * as controls.ts).
 */

import { TIER_COLOR, TIER_LABEL } from "./streams";
import { poiIconHtml } from "./poi-icons";

const TIER_ORDER: StreamTier[] = [
  "gold",
  "class1",
  "class2",
  "class3",
  "unclassified",
];

/** Short line-squiggle swatch -- streams are LINES on the map, so the
 *  legend shows a stroked line, not a dot. `color` may be a hex or a
 *  CSS var() (style attr, since var() is invalid in a stroke attr). */
function lineSwatch(color: string): string {
  return (
    `<svg class="legend-line" width="22" height="14" viewBox="0 0 22 14" aria-hidden="true">` +
    `<path d="M1 9 C 5 3, 9 13, 13 7 S 19 4, 21 6" fill="none" ` +
    `style="stroke:${color}" stroke-width="2.5" stroke-linecap="round"/></svg>`
  );
}

function renderTiers(): void {
  const host = document.getElementById("legend-tiers");
  if (!host) return;
  const rows = TIER_ORDER.map(
    (t) => `<div class="legend-item">${lineSwatch(TIER_COLOR[t])} ${TIER_LABEL[t]}</div>`,
  );
  // The faint USGS hydro overlay (lyr-usgs) is a line layer too.
  rows.push(
    `<div class="legend-item">${lineSwatch("var(--bl-river-300)")} All waterways</div>`,
  );
  host.innerHTML = rows.join("");
}

// POI rows mirror the map markers (makePoiElement) at legend size.
// Saved pins keep their copper-teardrop swatch (.legend-dot--pin) --
// distinct, user-owned, not a poi-icons disc.
const POINT_ROWS: Array<[type: string, label: string]> = [
  ["boat_ramp", "Boat ramp"],
  ["walk_in", "Walk-in access"],
  ["wading_access", "Wading access"],
  ["pier", "Pier"],
  ["parking", "Parking"],
  ["stocked", "Stocked water"],
  ["gauge", "USGS gauge"],
];

function renderPoints(): void {
  const host = document.getElementById("legend-points");
  if (!host) return;
  host.innerHTML =
    POINT_ROWS.map(
      ([t, label]) => `<div class="legend-item">${poiIconHtml(t, 18)} ${label}</div>`,
    ).join("") +
    `<div class="legend-item"><div class="legend-dot legend-dot--pin"></div> Saved pin</div>`;
}

/** Filters-pane rows marked data-poi get the same glyph disc as the
 *  map marker + legend row, so all three stay in sync. The saved-pins
 *  row goes copper -- its map marker is the copper teardrop, not a
 *  brand-blue POI disc. */
function stampPoiRows(): void {
  document
    .querySelectorAll<HTMLElement>(".filter-row-icon[data-poi]")
    .forEach((el) => {
      const type = el.dataset.poi || "";
      el.innerHTML = poiIconHtml(type, 18);
      if (type === "pin") el.firstElementChild?.classList.add("poi-icon--pin");
    });
}

renderTiers();
renderPoints();
stampPoiRows();
