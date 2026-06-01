/**
 * Saved pins on MapLibre GL JS (PR B2): teardrop HTML markers, the
 * load-from-API fetcher, and the drop-a-pin UI state machine.
 *
 * Cross-module deps:
 *   - maplibregl (Marker) + map, onMapReady from map-setup
 *   - makePopup from popups
 *   - esc from util, DEVICE_HEADER from state
 */

import maplibregl, { Marker, Popup } from "maplibre-gl";
import { map } from "./map-setup";
import { makePopup } from "./popups";
import { esc } from "./util";
import { DEVICE_HEADER } from "./state";

interface PinEntry {
  id: number;
  marker: Marker;
  popup: Popup;
}

let pinMarkers: PinEntry[] = [];
let _pinsVisible = true; // lyr-pins default checked

export function setPinsVisible(on: boolean): void {
  _pinsVisible = on;
  for (const e of pinMarkers) {
    if (on) e.marker.addTo(map);
    else e.marker.remove();
  }
}

export function addPinMarker(p: Pin): void {
  const el = document.createElement("div");
  el.className = "bl-pin";
  el.innerHTML = '<div class="bl-pin-dot"></div>';
  const popup = makePopup().setHTML(
    `<div class="pin-popup"><div class="pin-note">${esc(p.note || "(no note)")}</div>` +
      `<div class="pin-meta">${esc(p.created_at)}</div>` +
      `<button class="pin-del" type="button">Delete</button></div>`,
  );
  // Selecting a pin is a POI click -> close the rail panel.
  el.addEventListener("click", () =>
    document.dispatchEvent(new Event("bl:poi-open")),
  );
  const marker = new maplibregl.Marker({ element: el, anchor: "bottom" })
    .setLngLat([p.lon, p.lat])
    .setPopup(popup);
  const entry: PinEntry = { id: p.id, marker, popup };
  popup.on("open", () => {
    const btn = popup.getElement()?.querySelector<HTMLButtonElement>(".pin-del");
    if (!btn) return;
    btn.onclick = async () => {
      await fetch(`/api/pins/${p.id}`, { method: "DELETE", headers: DEVICE_HEADER });
      marker.remove();
      pinMarkers = pinMarkers.filter((e) => e !== entry);
      popup.remove();
    };
  });
  pinMarkers.push(entry);
  if (_pinsVisible) marker.addTo(map);
}

export async function loadPins(): Promise<void> {
  const pins: Pin[] = await fetch("/api/pins", { headers: DEVICE_HEADER }).then(
    (r) => r.json(),
  );
  for (const e of pinMarkers) e.marker.remove();
  pinMarkers = [];
  (pins || []).forEach(addPinMarker);
}

// -- Drop-a-pin interaction -----------------------------------------

let pinMode = false;
let pendingLngLat: maplibregl.LngLat | null = null;

const dropBtn = document.getElementById("drop-pin") as HTMLButtonElement;
const pinForm = document.getElementById("pin-form") as HTMLElement;
const pinNote = document.getElementById("pin-note") as HTMLTextAreaElement;

function setPinMode(on: boolean): void {
  pinMode = on;
  dropBtn.classList.toggle("active", on);
  map.getCanvas().style.cursor = on ? "crosshair" : "";
}

dropBtn.onclick = () => setPinMode(!pinMode);

map.on("click", (e) => {
  if (!pinMode) return;
  pendingLngLat = e.lngLat;
  pinNote.value = "";
  pinForm.hidden = false;
  pinNote.focus();
});

(document.getElementById("pin-cancel") as HTMLButtonElement).onclick = () => {
  pinForm.hidden = true;
  pendingLngLat = null;
};

(document.getElementById("pin-save") as HTMLButtonElement).onclick = async () => {
  if (!pendingLngLat) return;
  const res = await fetch("/api/pins", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...DEVICE_HEADER },
    body: JSON.stringify({
      lat: pendingLngLat.lat,
      lon: pendingLngLat.lng,
      note: pinNote.value,
    }),
  });
  if (res.ok) {
    addPinMarker((await res.json()) as Pin);
  }
  pinForm.hidden = true;
  pendingLngLat = null;
  setPinMode(false);
};

// -- Window bridge --------------------------------------------------

declare global {
  interface Window {
    loadPins: typeof loadPins;
    addPinMarker: typeof addPinMarker;
    setPinsVisible: typeof setPinsVisible;
  }
}

window.loadPins = loadPins;
window.addPinMarker = addPinMarker;
window.setPinsVisible = setPinsVisible;
