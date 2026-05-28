/**
 * 1-year USGS gauge trend renderer + hover-tooltip wirer. Dependency-
 * free SVG; no Leaflet, no charting library. Extracted from the
 * legacy app.js (PR B1c).
 *
 * Surface:
 *   - sparkline(series): returns the HTML string (used inside the
 *     river-detail panel and gauge popups).
 *   - wireSparkHover(root): finds any .bl-spark elements under root
 *     and attaches the crosshair + tooltip handlers. Idempotent so
 *     it's safe to call after every panel re-render.
 *
 * Same window-bridge pattern as util.ts / state.ts; the still-
 * monolithic app.js gets the duplicates removed and rebinds via
 * `const sparkline = window.sparkline;`.
 */

import { esc } from "./util";

/**
 * Decode HTML entities baked into a source string (USGS series names
 * arrive as "Streamflow, ft&#179;/s"). Letting esc() run on that
 * produces "&amp;#179;" which renders as literal "&#179;" in the
 * panel.
 */
function _decodeHtml(s: unknown): string {
  const d = document.createElement("div");
  d.innerHTML = String(s == null ? "" : s);
  return d.textContent || "";
}

/**
 * USGS unit codes are plain ASCII ("ft3/s", "deg C", "ft"). Promote
 * trailing digits to Unicode superscripts so the hover tip reads
 * like the chart label ("ft3/s" -> "ft³/s"). Safe because USGS
 * unit codes never use digits for anything but exponents.
 */
function _prettyUnit(u: string | null | undefined): string {
  return (u || "").replace(/\d/g, (d) => "⁰¹²³⁴⁵⁶⁷⁸⁹"[Number(d)] || d);
}

/**
 * Render a 1-yr trend sparkline as an HTML string. Returns a small
 * "no data" message if the series is empty or too short to chart.
 *
 * The HTML embeds a `<script type="application/json">` carrying the
 * per-point [svgX, svgY, value, date] data so wireSparkHover() can
 * pick it up without a separate ajax call. <script type="application/
 * json"> is inert (not executed) but its textContent is readable.
 */
export function sparkline(series: HistorySeries[] | null | undefined): string {
  if (!series || !series.length) {
    return '<div class="bl-trend-msg">No 1-yr data for this site.</div>';
  }
  // 00060 = USGS streamflow parameter code. Prefer it over temp /
  // gauge-height / etc. when the response carries several series.
  const s =
    series.find((x) => (x as { parameter?: string }).parameter === "00060") ||
    series[0];
  const pts = s.points || [];
  if (pts.length < 2) {
    return '<div class="bl-trend-msg">Not enough data to chart.</div>';
  }
  const vals = pts.map((p) => p.value as number);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const W = 300;
  const H = 80;
  const PX = 4;
  const PY = 6;
  const n = pts.length;
  const xs = pts.map((_, i) => PX + (i * (W - 2 * PX)) / (n - 1));
  const ys = pts.map((p) =>
    max === min
      ? H / 2
      : H - PY - (((p.value as number) - min) * (H - 2 * PY)) / (max - min),
  );
  let d = "";
  pts.forEach((_p, i) => {
    d += (i ? "L" : "M") + xs[i].toFixed(1) + " " + ys[i].toFixed(1) + " ";
  });
  const last = pts[pts.length - 1];
  const cleanName = _decodeHtml(
    (s as { name?: string }).name || (s as { parameter?: string }).parameter || "",
  );
  // Carry per-point [svgX, svgY, value, date] into the wired hover handler.
  const data = pts.map((p, i) => [
    xs[i],
    ys[i],
    p.value as number,
    (p.date || "").slice(0, 10),
  ]);
  return (
    `<div class="bl-trend-msg">${esc(cleanName)} &mdash; last 12 months</div>` +
    `<div class="bl-spark" data-w="${W}" data-h="${H}" ` +
    `data-unit="${esc(s.unit || "")}">` +
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" ` +
    `xmlns="http://www.w3.org/2000/svg">` +
    `<path d="${d}" fill="none" stroke="#2c6fbf" stroke-width="1.5"/>` +
    `<line class="bl-spark-cur" x1="0" x2="0" y1="0" y2="${H}" ` +
    `stroke="#94a3b8" stroke-dasharray="3 3" stroke-width="1" ` +
    `vector-effect="non-scaling-stroke" visibility="hidden"/>` +
    `<circle class="bl-spark-dot" r="3" fill="#2c6fbf" visibility="hidden"/>` +
    `</svg>` +
    `<div class="bl-spark-tip" hidden></div>` +
    // Inline data for the post-render wirer; <script type="application/json">
    // is inert (not executed) but readable via textContent.
    `<script type="application/json" class="bl-spark-data">${JSON.stringify(data)}</script>` +
    `</div>` +
    `<div class="bl-trend-msg">min ${min.toFixed(0)} &middot; ` +
    `max ${max.toFixed(0)} &middot; now ${(last.value as number).toFixed(0)} ` +
    `(${esc((last.date || "").slice(0, 10))})</div>`
  );
}

/**
 * Wire crosshair + tooltip readout on any .bl-spark inside `root`.
 * Idempotent (skips boxes already wired) so it can run after every
 * panel render without leaking handlers. Handles mouse + touch.
 */
export function wireSparkHover(root: Element | null): void {
  if (!root) return;
  root.querySelectorAll<HTMLElement>(".bl-spark").forEach((box) => {
    if (box.dataset.hover) return;
    box.dataset.hover = "1";
    const svg = box.querySelector<SVGSVGElement>("svg");
    const dataEl = box.querySelector<HTMLElement>(".bl-spark-data");
    const cur = box.querySelector<SVGLineElement>(".bl-spark-cur");
    const dot = box.querySelector<SVGCircleElement>(".bl-spark-dot");
    const tip = box.querySelector<HTMLElement>(".bl-spark-tip");
    if (!svg || !dataEl || !cur || !dot || !tip) return;
    let pts: Array<[number, number, number, string]>;
    try {
      pts = JSON.parse(dataEl.textContent || "[]");
    } catch (_) {
      return;
    }
    if (!pts.length) return;
    const W = parseFloat(box.dataset.w || "0");
    const H = parseFloat(box.dataset.h || "0");
    const unit = _prettyUnit(box.dataset.unit || "");

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
      const vx = ((clientX - rect.left) / rect.width) * W;
      // pts are spaced uniformly in svgX, so we can index directly.
      const i = Math.max(
        0,
        Math.min(
          pts.length - 1,
          Math.round(
            (vx - pts[0][0]) /
              ((pts[pts.length - 1][0] - pts[0][0]) / (pts.length - 1)),
          ),
        ),
      );
      const [px, py, val, date] = pts[i];
      cur.setAttribute("x1", String(px));
      cur.setAttribute("x2", String(px));
      cur.setAttribute("visibility", "visible");
      dot.setAttribute("cx", String(px));
      dot.setAttribute("cy", String(py));
      dot.setAttribute("visibility", "visible");
      tip.textContent = unit
        ? `${val.toFixed(0)} ${unit} (${date})`
        : `${val.toFixed(0)} (${date})`;
      tip.hidden = false;
      // Place tip in container px (svg viewBox -> rendered ratio).
      const tipX = (px / W) * rect.width;
      const tipY = (py / H) * rect.height;
      const tw = tip.offsetWidth || 80;
      tip.style.left =
        Math.max(0, Math.min(rect.width - tw, tipX + 6)) + "px";
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

// -- Window bridge for legacy app.js ---------------------------------

declare global {
  interface Window {
    sparkline: typeof sparkline;
    wireSparkHover: typeof wireSparkHover;
  }
}

window.sparkline = sparkline;
window.wireSparkHover = wireSparkHover;
