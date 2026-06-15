/**
 * "Directions" deep links. Opens the platform's maps app with the given
 * coordinate as the destination and the device's current location as the
 * origin: Apple Maps on iOS/iPadOS, Google Maps everywhere else (Android
 * opens the app, desktop opens the web map). Both are universal HTTPS URLs,
 * so there's no app-installed assumption.
 *
 * Two entry points:
 *   - directionsLinkHtml(lat, lon)  -- a resolved <a> for the client-built
 *     POI / pin popups (innerHTML strings; the client knows its platform at
 *     build time, so the href is final).
 *   - hydrateDirections(root)       -- fills the href on server-rendered
 *     `a.bl-dir-btn[data-dir-lat][data-dir-lon]` placeholders (the river
 *     panel card is server HTML, so the platform-specific href is set on the
 *     client after it mounts).
 */

function isIOS(): boolean {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod/.test(ua)) return true;
  // iPadOS 13+ reports a Mac UA; distinguish it by touch support.
  if (/Macintosh/.test(ua) && (navigator.maxTouchPoints || 0) > 1) return true;
  return false;
}

/** Platform-appropriate maps "directions to here" URL. */
export function directionsUrl(lat: number, lon: number): string {
  const dest = `${lat.toFixed(6)},${lon.toFixed(6)}`;
  return isIOS()
    ? `https://maps.apple.com/?daddr=${dest}&dirflg=d`
    : `https://www.google.com/maps/dir/?api=1&destination=${dest}`;
}

// Lucide "navigation" glyph, inlined (popup HTML isn't run through the Lucide
// hydration pass, so no <i data-lucide> here).
const NAV_SVG =
  '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
  'stroke-linejoin="round" aria-hidden="true">' +
  '<polygon points="3 11 22 2 13 21 11 13 3 11"/></svg>';

/** A resolved Directions link for client-built popups (access / stocked /
 *  dam / public-land / trail / pin). */
export function directionsLinkHtml(lat: number, lon: number): string {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "";
  return (
    `<div class="ap-link ap-dir"><a href="${directionsUrl(lat, lon)}" ` +
    `target="_blank" rel="noopener noreferrer">${NAV_SVG} Directions</a></div>`
  );
}

/** Set the href on server-rendered directions placeholders within `root`. */
export function hydrateDirections(root: ParentNode | null | undefined): void {
  if (!root) return;
  root
    .querySelectorAll<HTMLAnchorElement>(
      "a.bl-dir-btn[data-dir-lat][data-dir-lon]",
    )
    .forEach((a) => {
      if (a.dataset.dirHydrated) return;
      const lat = parseFloat(a.dataset.dirLat || "");
      const lon = parseFloat(a.dataset.dirLon || "");
      if (Number.isFinite(lat) && Number.isFinite(lon)) {
        a.href = directionsUrl(lat, lon);
        a.dataset.dirHydrated = "1";
      } else {
        a.remove(); // no usable coordinate -> drop the button
      }
    });
}
