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
  host.innerHTML = rows.join("");
}

// POI rows mirror the map markers (makePoiElement) at legend size, grouped
// under the same labels as the Map Layers "Show on map" sections so the two
// panes read identically. Saved pins keep their copper-teardrop swatch
// (.legend-dot--pin) -- distinct, user-owned, not a poi-icons disc -- and
// render in their own "My pins" group below.
const POINT_GROUPS: Array<[section: string, rows: Array<[type: string, label: string]>]> = [
  [
    "Access & facilities",
    [
      ["boat_ramp", "Boat ramp"],
      ["fishing_access", "Fishing access"],
      ["pier", "Pier"],
      ["parking", "Parking"],
    ],
  ],
  [
    "Water features",
    [
      ["stocked", "Stocked water"],
      ["dam", "Dam"],
      ["gauge", "USGS gauge"],
    ],
  ],
];

function renderPoints(): void {
  const host = document.getElementById("legend-points");
  if (!host) return;
  const groups = POINT_GROUPS.map(
    ([section, rows]) =>
      `<section class="panel-section">` +
      `<div class="panel-section-label">${section}</div>` +
      rows
        .map(([t, label]) => `<div class="legend-item">${poiIconHtml(t, 18)} ${label}</div>`)
        .join("") +
      `</section>`,
  ).join("");
  host.innerHTML =
    groups +
    `<section class="panel-section">` +
    `<div class="panel-section-label">My pins</div>` +
    `<div class="legend-item"><div class="legend-dot legend-dot--pin"></div> Saved pin</div>` +
    `</section>`;
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
