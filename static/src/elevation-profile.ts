/**
 * Stream elevation / gradient profile: the "Gradient" tab in the river
 * panel. Dependency-free SVG (same approach as sparkline.ts) plus the
 * lazy loader that fetches /api/elevation_profile on panel open.
 *
 * Surface:
 *   - renderElevationProfile(p): HTML string -- a 5-up summary stat row
 *     (length / drop / grade / high / low) over an area chart of
 *     elevation-vs-distance, with the clicked reach marked.
 *   - wireElevHover(root): crosshair + "433' · 8.9 mi" readout on any
 *     .bl-elev-chart under root (idempotent).
 *   - autoLoadElevation(root, keys): finds the .bl-elev placeholder the
 *     server rendered into the Gradient tab, fetches the profile by
 *     comid (preferred) or levelpathid+name, and fills it -- or shows a
 *     graceful "not available" note (e.g. a region not yet in the VAA).
 */

import { esc } from "./util";

export interface ElevKeys {
  comid?: number | null;
  levelpathid?: number | null;
  name?: string | null;
}

const W = 300;
const H = 96;
const PX = 4;
const PY_TOP = 10;
const PY_BOT = 6;

function _statCell(value: string, unit: string, label: string): string {
  return (
    `<div class="bl-stat"><div class="bl-stat-n bl-num">${esc(value)}` +
    (unit ? `<span class="bl-elev-unit">${esc(unit)}</span>` : "") +
    `</div><div class="bl-stat-label">${esc(label)}</div></div>`
  );
}

/** Render the profile summary + area chart as an HTML string. */
export function renderElevationProfile(p: ElevationProfile): string {
  const pts = p.points || [];
  if (pts.length < 2) {
    return '<div class="bl-reach-msg">Not enough elevation data to chart.</div>';
  }
  const ds = pts.map((q) => q.d);
  const es = pts.map((q) => q.e);
  const maxD = Math.max(...ds) || 1;
  const minE = Math.min(...es);
  const maxE = Math.max(...es);
  const xOf = (d: number) => PX + (d / maxD) * (W - 2 * PX);
  const yOf = (e: number) =>
    maxE === minE
      ? H / 2
      : H - PY_BOT - ((e - minE) * (H - PY_TOP - PY_BOT)) / (maxE - minE);

  let line = "";
  pts.forEach((q, i) => {
    line += (i ? "L" : "M") + xOf(q.d).toFixed(1) + " " + yOf(q.e).toFixed(1) + " ";
  });
  const base = (H - PY_BOT).toFixed(1);
  const area =
    `M${xOf(0).toFixed(1)} ${base} ` +
    pts.map((q) => `L${xOf(q.d).toFixed(1)} ${yOf(q.e).toFixed(1)} `).join("") +
    `L${xOf(maxD).toFixed(1)} ${base} Z`;

  // Clicked-reach marker (vertical guide + dot + label), when present.
  let marker = "";
  if (p.focus) {
    const fx = xOf(p.focus.d);
    const fy = yOf(p.focus.e);
    marker =
      `<line x1="${fx.toFixed(1)}" x2="${fx.toFixed(1)}" y1="0" y2="${H}" ` +
      `stroke="#0b1622" stroke-dasharray="3 3" stroke-width="1" ` +
      `vector-effect="non-scaling-stroke" opacity="0.5"/>` +
      `<circle cx="${fx.toFixed(1)}" cy="${fy.toFixed(1)}" r="3.5" ` +
      `fill="#0b1622"/>`;
  }

  // [svgX, svgY, dist_mi, elev_ft] per point for the hover wirer.
  const data = pts.map((q) => [xOf(q.d), yOf(q.e), q.d, q.e]);

  const stats =
    '<div class="bl-stats bl-elev-stats">' +
    _statCell(String(p.length_mi), "mi", "Length") +
    _statCell(String(p.elev_change_ft), "ft", "Elev. change") +
    _statCell(String(p.grade_ft_per_mi), "ft/mi", "Avg. grade") +
    _statCell(String(p.high_ft), "ft", "High point") +
    _statCell(String(p.low_ft), "ft", "Low point") +
    "</div>";

  return (
    stats +
    `<div class="bl-elev-chart" data-w="${W}" data-h="${H}">` +
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" ` +
    `xmlns="http://www.w3.org/2000/svg">` +
    `<path d="${area}" fill="#2c6fbf" fill-opacity="0.12" stroke="none"/>` +
    `<path d="${line}" fill="none" stroke="#2c6fbf" stroke-width="1.5" ` +
    `vector-effect="non-scaling-stroke"/>` +
    marker +
    `<line class="bl-elev-cur" x1="0" x2="0" y1="0" y2="${H}" ` +
    `stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="1" ` +
    `vector-effect="non-scaling-stroke" visibility="hidden"/>` +
    `<circle class="bl-elev-dot" r="3" fill="#2c6fbf" visibility="hidden"/>` +
    `</svg>` +
    `<div class="bl-spark-tip" hidden></div>` +
    `<script type="application/json" class="bl-elev-data">${JSON.stringify(data)}</script>` +
    `</div>` +
    `<div class="bl-trend-msg">${esc(p.name)} &mdash; ${p.reach_count} ` +
    `reaches &middot; downstream &rarr;</div>`
  );
}

/** Crosshair + "elev' · dist mi" readout on any .bl-elev-chart in root. */
export function wireElevHover(root: Element | null): void {
  if (!root) return;
  root.querySelectorAll<HTMLElement>(".bl-elev-chart").forEach((box) => {
    if (box.dataset.hover) return;
    box.dataset.hover = "1";
    const svg = box.querySelector<SVGSVGElement>("svg");
    const dataEl = box.querySelector<HTMLElement>(".bl-elev-data");
    const cur = box.querySelector<SVGLineElement>(".bl-elev-cur");
    const dot = box.querySelector<SVGCircleElement>(".bl-elev-dot");
    const tip = box.querySelector<HTMLElement>(".bl-spark-tip");
    if (!svg || !dataEl || !cur || !dot || !tip) return;
    let pts: Array<[number, number, number, number]>;
    try {
      pts = JSON.parse(dataEl.textContent || "[]");
    } catch (_) {
      return;
    }
    if (!pts.length) return;
    const w = parseFloat(box.dataset.w || "0");
    const h = parseFloat(box.dataset.h || "0");

    const hide = () => {
      cur.setAttribute("visibility", "hidden");
      dot.setAttribute("visibility", "hidden");
      tip.hidden = true;
    };
    const move = (clientX: number) => {
      const rect = svg.getBoundingClientRect();
      if (clientX < rect.left || clientX > rect.right) {
        hide();
        return;
      }
      const vx = ((clientX - rect.left) / rect.width) * w;
      // Points are NOT uniformly spaced in X (reach lengths vary) -> find
      // the nearest by svgX rather than indexing arithmetically.
      let best = 0;
      let bestDx = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const dx = Math.abs(pts[i][0] - vx);
        if (dx < bestDx) {
          bestDx = dx;
          best = i;
        }
      }
      const [px, py, dist, elev] = pts[best];
      cur.setAttribute("x1", String(px));
      cur.setAttribute("x2", String(px));
      cur.setAttribute("visibility", "visible");
      dot.setAttribute("cx", String(px));
      dot.setAttribute("cy", String(py));
      dot.setAttribute("visibility", "visible");
      tip.textContent = `${Math.round(elev)}' · ${dist.toFixed(1)} mi`;
      tip.hidden = false;
      const tipX = (px / w) * rect.width;
      const tipY = (py / h) * rect.height;
      const tw = tip.offsetWidth || 80;
      tip.style.left = Math.max(0, Math.min(rect.width - tw, tipX + 6)) + "px";
      tip.style.top = Math.max(0, tipY - 22) + "px";
    };
    svg.addEventListener("mousemove", (e) => move(e.clientX));
    svg.addEventListener("mouseleave", hide);
    svg.addEventListener(
      "touchstart",
      (e) => {
        if (e.touches[0]) move(e.touches[0].clientX);
      },
      { passive: true },
    );
    svg.addEventListener(
      "touchmove",
      (e) => {
        if (e.touches[0]) move(e.touches[0].clientX);
      },
      { passive: true },
    );
    svg.addEventListener("touchend", hide);
    svg.addEventListener("touchcancel", hide);
  });
}

/**
 * Fill the Gradient tab's .bl-elev placeholder with the profile for the
 * clicked reach. Keyed by comid (preferred) or levelpathid + name. A
 * sequence guard drops a stale response if another reach is opened first.
 */
let _elevSeq = 0;

export async function autoLoadElevation(
  root: HTMLElement,
  keys: ElevKeys,
): Promise<void> {
  const box = root.querySelector<HTMLElement>(".bl-elev");
  if (!box) return;
  // Drop the whole "Elevation profile" section when there's no data, so
  // the Conditions tab doesn't carry an empty stub (the common case until
  // the national VAA rebuild). Falls back to clearing the box.
  const drop = () => {
    const section = box.closest(".bl-elev-section");
    if (section) section.remove();
    else box.innerHTML = "";
  };
  const seq = ++_elevSeq;
  const q = new URLSearchParams();
  if (keys.comid != null) q.set("comid", String(keys.comid));
  if (keys.levelpathid != null) q.set("levelpathid", String(keys.levelpathid));
  if (keys.name) q.set("name", keys.name);
  if (![...q.keys()].length) {
    drop();
    return;
  }
  // Placeholder while the profile loads -- the box used to sit empty for
  // the whole round-trip, then pop (or silently vanish via drop()).
  box.innerHTML = '<div class="bl-trend-msg">Loading gradient&hellip;</div>';
  let data: ElevationProfile | null = null;
  let ok = false;
  try {
    const r = await fetch(`/api/elevation_profile?${q.toString()}`);
    ok = r.ok;
    if (ok) data = (await r.json()) as ElevationProfile;
  } catch (_) {
    ok = false;
  }
  if (seq !== _elevSeq) return; // superseded by a newer open
  if (!ok || !data) {
    drop();
    return;
  }
  box.innerHTML = renderElevationProfile(data);
  wireElevHover(box);
}
