/**
 * Leaflet type augmentations. Lives in a SEPARATE file from
 * static/types.d.ts because:
 *
 *   - types.d.ts is an ambient SCRIPT file (no top-level imports /
 *     exports) so its global type declarations are visible everywhere
 *     without anyone needing to `import` them. That's the point.
 *   - But `declare module "leaflet"` inside a script file REPLACES
 *     the existing module declaration from @types/leaflet (rather
 *     than augmenting it) -- and replacing leaves only the
 *     augmentations, so `L.Map`, `L.TileLayer`, etc. all vanish.
 *   - `declare module "..."` only AUGMENTS when it's inside a MODULE
 *     file. This file is a module (has `export {}` at the bottom)
 *     specifically so the augmentation merges properly.
 *
 * What this augments: the legacy app.js attaches `_blRiver` to
 * markers + GeoJSON layers so a click handler can recover the parent
 * River from the layer. Tell TypeScript about it so the access
 * pattern isn't flagged.
 */

declare module "leaflet" {
  interface Marker {
    _blRiver?: River;
  }
  interface GeoJSON {
    _blRiver?: River;
  }
  interface Layer {
    _blRiver?: River;
  }
}

export {};
