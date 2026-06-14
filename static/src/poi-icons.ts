/**
 * POI marker glyphs + element factories (TroutRoutes-style markers):
 * one brand-blue disc, a white glyph keyed by POI type, white ring +
 * subtle shadow. Type is the glyph, not a color code -- the per-type
 * colored letter pins this replaces made every color carry a different
 * meaning per layer.
 *
 * Glyph path artwork is copied from Lucide (https://lucide.dev, ISC
 * license) and inlined -- markers are built in tight per-feature loops
 * and must render offline, so no data-lucide hydration here. `pier` and
 * `dam` are hand-drawn in the same 24x24 / stroke-2 style (Lucide has no
 * pier/dock or dam icon).
 */

export type PoiType =
  | "boat_ramp"
  | "walk_in"
  | "wading_access"
  | "pier"
  | "parking"
  | "stocked"
  | "dam"
  | "gauge"
  | "pin";

// Inner SVG markup per type (24x24 viewBox, stroke-rendered).
const GLYPH_PATHS: Record<PoiType, string> = {
  // Lucide "sailboat"
  boat_ramp:
    '<path d="M22 18H2a4 4 0 0 0 4 4h12a4 4 0 0 0 4-4Z"/>' +
    '<path d="M21 14 10 2 3 14h18Z"/>' +
    '<path d="M10 2v16"/>',
  // Lucide "footprints"
  walk_in:
    '<path d="M4 16v-2.38C4 11.5 2.97 10.5 3 8c.03-2.72 1.49-6 4.5-6C9.37 2 10 3.8 10 5.5c0 3.11-2 5.66-2 8.68V16a2 2 0 1 1-4 0Z"/>' +
    '<path d="M20 20v-2.38c0-2.12 1.03-3.12 1-5.62-.03-2.72-1.49-6-4.5-6C14.63 6 14 7.8 14 9.5c0 3.11 2 5.66 2 8.68V20a2 2 0 1 0 4 0Z"/>' +
    '<path d="M16 17h4"/>' +
    '<path d="M4 13h4"/>',
  // Lucide "waves"
  wading_access:
    '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>' +
    '<path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>' +
    '<path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
  // Hand-drawn dock: deck line over three pilings (Lucide stroke style).
  pier:
    '<path d="M3 8h18"/>' +
    '<path d="M5 8v11"/>' +
    '<path d="M12 8v11"/>' +
    '<path d="M19 8v11"/>',
  // Lucide "circle-parking" (the P is a path, not text)
  parking:
    '<circle cx="12" cy="12" r="10"/>' +
    '<path d="M9 17V7h4a3 3 0 0 1 0 6H9"/>',
  // Lucide "fish"
  stocked:
    '<path d="M6.5 12c.94-3.46 4.94-6 8.5-6 3.56 0 6.06 2.54 7 6-.94 3.47-3.44 6-7 6s-7.56-2.53-8.5-6Z"/>' +
    '<path d="M18 12v.5"/>' +
    '<path d="M16 17.93a9.77 9.77 0 0 1 0-11.86"/>' +
    '<path d="M7 10.67C7 8 5.58 5.97 2.73 5.5c-1 1.5-1 5 .23 6.5-1.24 1.5-1.24 5-.23 6.5C5.58 18.03 7 16 7 13.33"/>' +
    '<path d="M10.46 7.26C10.2 5.88 9.17 4.24 8 3h5.8a2 2 0 0 1 1.98 1.67l.23 1.4"/>' +
    '<path d="m16.01 17.93-.23 1.4A2 2 0 0 1 13.8 21H9.5a5.96 5.96 0 0 0 1.49-3.98"/>',
  // Hand-drawn dam cross-section: impounded water (two short waves) at
  // left, a sloped wall down to a wider base at right (Lucide stroke style).
  dam:
    '<path d="M3 9c1.2 1 2.4 1 3.6 0s2.4-1 3.6 0"/>' +
    '<path d="M3 13c1.2 1 2.4 1 3.6 0s2.4-1 3.6 0"/>' +
    '<path d="M12 5v14"/>' +
    '<path d="M12 5l5 14"/>' +
    '<path d="M10 19h9"/>',
  // Lucide "droplet"
  gauge:
    '<path d="M12 22a7 7 0 0 0 7-7c0-2-1-3.9-3-5.5s-3.5-4-4-6.5c-.5 2.5-2 4.9-4 6.5C6 11.1 5 13 5 15a7 7 0 0 0 7 7z"/>',
  // Lucide "map-pin"
  pin:
    '<path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0Z"/>' +
    '<circle cx="12" cy="10" r="3"/>',
};

/** Bare white-stroke glyph SVG. Unknown types fall back to walk_in. */
export function poiGlyphSvg(type: string, px: number): string {
  const inner = GLYPH_PATHS[type as PoiType] || GLYPH_PATHS.walk_in;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="${px}" ` +
    `viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${inner}</svg>`
  );
}

/** The on-map marker element (brand-blue disc, white glyph; styled by
 *  .poi-marker / .poi-marker-pin in app.css). */
export function makePoiElement(type: string): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "poi-marker";
  wrap.innerHTML = `<div class="poi-marker-pin">${poiGlyphSvg(type, 13)}</div>`;
  return wrap;
}

/** The same disc shrunk for legend rows + Filters-panel layer rows. */
export function poiIconHtml(type: string, sizePx = 18): string {
  const glyph = poiGlyphSvg(type, Math.round(sizePx * 0.6));
  return (
    `<span class="poi-icon" style="width:${sizePx}px;height:${sizePx}px">` +
    `${glyph}</span>`
  );
}
