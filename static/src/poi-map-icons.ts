/**
 * POI marker glyphs as MapLibre map images, for the vector-tile point overlays
 * (access / dams / stocking). The old DOM-marker layers built a `makePoiElement`
 * disc per feature; as GPU symbol layers we instead register each disc+glyph
 * once as a named image ("poi-<type>") that a symbol layer references via
 * `icon-image`. Rasterized from the shared `poi-icons.ts` glyph paths so the
 * on-map look is identical to the markers they replace (brand-blue disc, white
 * ring, white glyph).
 */

import { map } from "./map-setup";
import { poiGlyphSvg } from "./poi-icons";

// Match the DOM marker (.poi-marker-pin): --bl-river-700 disc, white ring +
// glyph. Hard-coded (not read from CSS) because we rasterize off-DOM.
const DISC = "#15506C";
const RING = "#FFFFFF";
const GLYPH = "#FFFFFF";
const SIZE = 48; // physical px; addImage pixelRatio 2 => 24 CSS px (marker size)
const GLYPH_PX = 26;

// The POI types that have a glyph + get a symbol icon. Mirrors PoiType in
// poi-icons.ts minus the ones that stay DOM markers (gauge/pin are the live +
// user layers, not tiled).
const POI_ICON_TYPES = [
  "boat_ramp",
  "fishing_access",
  "pier",
  "parking",
  "stocked",
  "dam",
] as const;

function markerSvg(type: string): string {
  // A brand disc with a nested glyph <svg> centered on top. Setting the group's
  // `color` makes the glyph's stroke="currentColor" resolve to white.
  const off = (SIZE - GLYPH_PX) / 2;
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${SIZE}" height="${SIZE}" ` +
    `viewBox="0 0 ${SIZE} ${SIZE}">` +
    `<circle cx="${SIZE / 2}" cy="${SIZE / 2}" r="21" fill="${DISC}" ` +
    `stroke="${RING}" stroke-width="3"/>` +
    `<g transform="translate(${off},${off})" color="${GLYPH}">` +
    `${poiGlyphSvg(type, GLYPH_PX)}</g>` +
    `</svg>`
  );
}

function rasterize(svg: string): Promise<ImageData> {
  return new Promise((resolve, reject) => {
    const img = new Image(SIZE, SIZE);
    img.onload = () => {
      const c = document.createElement("canvas");
      c.width = SIZE;
      c.height = SIZE;
      const ctx = c.getContext("2d");
      if (!ctx) {
        reject(new Error("no 2d context"));
        return;
      }
      ctx.drawImage(img, 0, 0, SIZE, SIZE);
      resolve(ctx.getImageData(0, 0, SIZE, SIZE));
    };
    img.onerror = () => reject(new Error("svg decode failed"));
    img.src =
      "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  });
}

let _registered: Promise<void> | null = null;

/** Register every "poi-<type>" image once. Idempotent and safe to await from
 *  multiple layer setups; returns the same in-flight promise. A single icon
 *  failing to rasterize is swallowed (that symbol just renders iconless). */
export function registerPoiIcons(): Promise<void> {
  if (_registered) return _registered;
  _registered = (async () => {
    for (const type of POI_ICON_TYPES) {
      const id = `poi-${type}`;
      if (map.hasImage(id)) continue;
      try {
        const data = await rasterize(markerSvg(type));
        if (map.hasImage(id)) continue;
        map.addImage(
          id,
          { width: SIZE, height: SIZE, data: new Uint8Array(data.data.buffer) },
          { pixelRatio: 2 },
        );
      } catch (_) {
        /* leave unregistered -> symbol renders without an icon, not a crash */
      }
    }
  })();
  return _registered;
}
