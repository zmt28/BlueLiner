/**
 * Saved pins: marker rendering, the load-from-API fetcher, the
 * drop-a-pin UI state machine. Extracted from app.js in PR B1j.
 *
 * Owns:
 *   - addPinMarker(p): creates one teardrop divIcon marker for a
 *     saved pin and adds it to the pinsLayer; binds the popup with
 *     the note + delete button.
 *   - loadPins(): fetches /api/pins for this device token, clears
 *     the pinsLayer, and re-creates one marker per pin.
 *   - setPinMode(on): toggles the drop-a-pin mode (cursor +
 *     drop-pin button highlight).
 *   - Module-init DOM wiring: drop-pin button click, map click in
 *     pin mode, pin form save/cancel buttons.
 *
 * Cross-module deps:
 *   - L from leaflet (divIcon + marker)
 *   - map from map-setup
 *   - pinsLayer from map-layers
 *   - esc, popupOpts from util
 *   - DEVICE_HEADER from state
 */

import * as L from "leaflet";
import { map } from "./map-setup";
import { pinsLayer } from "./map-layers";
import { esc, popupOpts } from "./util";
import { DEVICE_HEADER } from "./state";

export function addPinMarker(p: Pin): void {
  const icon = L.divIcon({
    className: "bl-pin",
    html: '<div class="bl-pin-dot"></div>',
    iconSize: [16, 16],
    iconAnchor: [8, 16],
  });
  const m = L.marker([p.lat, p.lon], { icon });
  m.bindPopup(
    `<div class="pin-popup"><div class="pin-note">${esc(p.note || "(no note)")}</div>` +
      `<div class="pin-meta">${esc(p.created_at)}</div>` +
      `<button class="pin-del" type="button">Delete</button></div>`,
    popupOpts(),
  );
  m.on("popupopen", (e: L.PopupEvent) => {
    const btn = e.popup.getElement()?.querySelector<HTMLButtonElement>(".pin-del");
    if (btn) {
      btn.onclick = async () => {
        await fetch(`/api/pins/${p.id}`, {
          method: "DELETE",
          headers: DEVICE_HEADER,
        });
        pinsLayer.removeLayer(m);
        map.closePopup();
      };
    }
  });
  pinsLayer.addLayer(m);
}

export async function loadPins(): Promise<void> {
  const pins: Pin[] = await fetch("/api/pins", {
    headers: DEVICE_HEADER,
  }).then((r) => r.json());
  pinsLayer.clearLayers();
  (pins || []).forEach(addPinMarker);
}

// -- Drop-a-pin interaction -----------------------------------------

let pinMode = false;
let pendingLatLng: L.LatLng | null = null;

const dropBtn = document.getElementById("drop-pin") as HTMLButtonElement;
const pinForm = document.getElementById("pin-form") as HTMLElement;
const pinNote = document.getElementById("pin-note") as HTMLTextAreaElement;

function setPinMode(on: boolean): void {
  pinMode = on;
  dropBtn.classList.toggle("active", on);
  map.getContainer().style.cursor = on ? "crosshair" : "";
}

dropBtn.onclick = () => setPinMode(!pinMode);

map.on("click", (e: L.LeafletMouseEvent) => {
  if (!pinMode) return;
  pendingLatLng = e.latlng;
  pinNote.value = "";
  pinForm.hidden = false;
  pinNote.focus();
});

(document.getElementById("pin-cancel") as HTMLButtonElement).onclick = () => {
  pinForm.hidden = true;
  pendingLatLng = null;
};

(document.getElementById("pin-save") as HTMLButtonElement).onclick = async () => {
  if (!pendingLatLng) return;
  const res = await fetch("/api/pins", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...DEVICE_HEADER },
    body: JSON.stringify({
      lat: pendingLatLng.lat,
      lon: pendingLatLng.lng,
      note: pinNote.value,
    }),
  });
  if (res.ok) {
    addPinMarker((await res.json()) as Pin);
  }
  pinForm.hidden = true;
  pendingLatLng = null;
  setPinMode(false);
};

// -- Window bridge for cross-module consumers (catches.ts after-claim
// reload). Drops once auth.ts moves to a direct ES import. ---------

declare global {
  interface Window {
    loadPins: typeof loadPins;
    addPinMarker: typeof addPinMarker;
  }
}

window.loadPins = loadPins;
window.addPinMarker = addPinMarker;
