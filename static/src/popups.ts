/**
 * MapLibre popup + tooltip helpers. Replaces Leaflet's bindPopup /
 * bindTooltip + the old util.popupOpts().
 *
 *   - makePopup()          one configured maplibregl.Popup, sized to the
 *                          viewport, that hydrates Lucide icons on open
 *                          (replaces map.on("popupopen", refreshIcons)).
 *   - createLayerTooltip() hover tooltip for GL *layers* (e.g. trout
 *                          stream names) — mousemove + shared popup.
 *   - createMarkerTooltip() hover tooltip for HTML *markers* (condition
 *                          + access markers are DOM elements, not layer
 *                          features, so the layer helper can't target them).
 */

import maplibregl, { Map as MaplibreMap, Popup } from "maplibre-gl";
import { refreshIcons } from "./util";

/** A popup sized to the current viewport. Tall content (hatch + several
 *  gauges) scrolls inside `.maplibregl-popup-content` (see app.css).
 *  Re-evaluated per call so a rotation between opens picks up new dims. */
export function makePopup(extra?: Partial<maplibregl.PopupOptions>): Popup {
  const maxW = Math.min(420, (window.innerWidth || 420) - 32);
  const p = new maplibregl.Popup({
    maxWidth: `${maxW}px`,
    closeButton: true,
    closeOnClick: true,
    ...extra,
  });
  // Hydrate freshly-injected <i data-lucide> nodes once the popup mounts.
  p.on("open", () => refreshIcons());
  return p;
}

type GetText = (props: Record<string, unknown>) => string | null;

/** Hover tooltip for one or more GL layers. One shared popup follows the
 *  cursor over the layer; hidden on mouseleave. Touch falls back to the
 *  click/popup flow, so we only wire pointer hover here. */
export function createLayerTooltip(map: MaplibreMap): {
  bind: (layerId: string, getText: GetText) => void;
} {
  const tip = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: "ml-tooltip",
  });
  return {
    bind(layerId, getText) {
      map.on("mousemove", layerId, (e) => {
        const f = e.features && e.features[0];
        if (!f) return;
        const text = getText(f.properties || {});
        if (!text) {
          tip.remove();
          return;
        }
        map.getCanvas().style.cursor = "pointer";
        tip.setLngLat(e.lngLat).setHTML(text).addTo(map);
      });
      map.on("mouseleave", layerId, () => {
        map.getCanvas().style.cursor = "";
        tip.remove();
      });
    },
  };
}

/** Hover tooltip for HTML markers. Attach to a marker's element + its
 *  lngLat; shows a shared popup on mouseenter, hides on mouseleave. */
export function createMarkerTooltip(map: MaplibreMap): {
  bind: (el: HTMLElement, lngLat: [number, number], html: string) => void;
} {
  const tip = new maplibregl.Popup({
    closeButton: false,
    closeOnClick: false,
    className: "ml-tooltip",
    offset: 14,
  });
  return {
    bind(el, lngLat, html) {
      el.addEventListener("mouseenter", () => {
        tip.setLngLat(lngLat).setHTML(html).addTo(map);
      });
      el.addEventListener("mouseleave", () => tip.remove());
    },
  };
}
