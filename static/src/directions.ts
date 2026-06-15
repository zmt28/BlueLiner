/**
 * "Directions" deep links. We surface BOTH Apple Maps and Google Maps and
 * let the user pick — some people prefer Google Maps even on iOS — rather
 * than guessing from the platform. Each link routes to the given coordinate
 * with the device's current location as the origin; both are universal HTTPS
 * URLs (the maps app opens on mobile, the web map on desktop). Because the
 * URLs are fully determined by the coordinate, there's no platform detection
 * and nothing to hydrate — server- and client-rendered surfaces emit the
 * same two links directly.
 */

export function appleMapsUrl(lat: number, lon: number): string {
  return `https://maps.apple.com/?daddr=${lat.toFixed(6)},${lon.toFixed(6)}&dirflg=d`;
}

export function googleMapsUrl(lat: number, lon: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat.toFixed(6)},${lon.toFixed(6)}`;
}

// Lucide "navigation" glyph, inlined (popup HTML isn't run through the Lucide
// hydration pass, so no <i data-lucide> here).
const NAV_SVG =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round" aria-hidden="true">' +
  '<polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>';

const _ATTRS = 'target="_blank" rel="noopener noreferrer"';

/** A compact Directions row offering both map apps, for client-built popups
 *  (access / stocked / dam / public-land / trail / pin). */
export function directionsLinkHtml(lat: number, lon: number): string {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "";
  return (
    `<div class="ap-dir">` +
    `<span class="ap-dir-label">${NAV_SVG} Directions</span>` +
    `<a href="${appleMapsUrl(lat, lon)}" ${_ATTRS}>Apple Maps</a>` +
    `<a href="${googleMapsUrl(lat, lon)}" ${_ATTRS}>Google Maps</a>` +
    `</div>`
  );
}
