/**
 * Saved pins on MapLibre GL JS (PR B2): teardrop HTML markers, the
 * load-from-API fetcher, and the drop-a-pin UI state machine.
 *
 * Drop-a-pin flow: arming pin mode shows a hint chip (touch has no
 * crosshair cursor, so the chip is the only "armed" signal there). The
 * first map click drops a draggable ghost pin at the click point and
 * opens the note form; further clicks (or dragging the ghost) move the
 * pending location without clearing a typed note. Save commits the
 * ghost as a real marker; Cancel/Esc discards it. While pin mode is
 * armed, feature click handlers (streams, POIs, lands, trails) stand
 * down via isPinPlacementActive() so placing a pin on a stream doesn't
 * also select the stream.
 *
 * Cross-module deps:
 *   - maplibregl (Marker) + map, onMapReady from map-setup
 *   - makePopup from popups
 *   - esc from util, DEVICE_HEADER from state
 *   - showToast / confirmDialog feedback primitives
 */

import maplibregl, { Marker, Popup } from "maplibre-gl";
import { map } from "./map-setup";
import { makePopup } from "./popups";
import { esc } from "./util";
import { DEVICE_HEADER } from "./state";
import { directionsLinkHtml } from "./directions";
import { showToast } from "./toast";
import { confirmDialog } from "./confirm";
import { claimMapClicks, releaseMapClicks } from "./map-mode";

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

/** created_at is a full ISO UTC timestamp (db.py); show a local date. */
function fmtCreated(s: string): string {
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function addPinMarker(p: Pin): void {
  const el = document.createElement("div");
  el.className = "bl-pin";
  el.innerHTML = '<div class="bl-pin-dot"></div>';
  const popup = makePopup().setHTML(
    `<div class="pin-popup"><div class="pin-note">${esc(p.note || "(no note)")}</div>` +
      `<div class="pin-meta">${esc(fmtCreated(p.created_at))}</div>` +
      directionsLinkHtml(p.lat, p.lon, p.note) +
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
      const ok = await confirmDialog({
        title: "Delete pin?",
        message: "This pin and its note will be permanently removed.",
        confirmLabel: "Delete",
        danger: true,
      });
      if (!ok) return;
      try {
        const res = await fetch(`/api/pins/${p.id}`, {
          method: "DELETE",
          headers: DEVICE_HEADER,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
      } catch {
        showToast("Couldn't delete the pin — try again.", "error");
        return; // keep the marker; the server still has it
      }
      marker.remove();
      pinMarkers = pinMarkers.filter((e) => e !== entry);
      popup.remove();
    };
  });
  pinMarkers.push(entry);
  if (_pinsVisible) marker.addTo(map);
}

export async function loadPins(): Promise<void> {
  let pins: Pin[];
  try {
    const r = await fetch("/api/pins", { headers: DEVICE_HEADER });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    pins = (await r.json()) as Pin[];
  } catch (err) {
    // Boot path: fail quietly and keep whatever's already on the map.
    console.warn("pins failed to load:", err);
    return;
  }
  for (const e of pinMarkers) e.marker.remove();
  pinMarkers = [];
  (pins || []).forEach(addPinMarker);
}

// -- Drop-a-pin interaction -----------------------------------------

let pinMode = false;
let pendingLngLat: maplibregl.LngLat | null = null;
let ghost: Marker | null = null;

const dropBtn = document.getElementById("drop-pin") as HTMLButtonElement;
const pinForm = document.getElementById("pin-form") as HTMLElement;
const pinNote = document.getElementById("pin-note") as HTMLTextAreaElement;
const pinError = document.getElementById("pin-error") as HTMLElement;
const pinSave = document.getElementById("pin-save") as HTMLButtonElement;
const pinCancel = document.getElementById("pin-cancel") as HTMLButtonElement;

/** True while pin mode is armed. Feature click handlers (streams,
 *  POIs, public lands, trails) check this and stand down so a placement
 *  click doesn't also select what's under it. */
export function isPinPlacementActive(): boolean {
  return pinMode;
}

let hint: HTMLElement | null = null;
function setHintVisible(on: boolean): void {
  if (on && !hint) {
    hint = document.createElement("div");
    hint.className = "bl-pin-hint";
    hint.textContent = "Tap the map to place a pin";
    document.body.appendChild(hint);
  }
  if (hint) hint.hidden = !on;
}

function showError(msg: string): void {
  pinError.textContent = msg;
  pinError.hidden = false;
}

/** Remove the ghost + pending state and close the form. */
function discardPlacement(): void {
  ghost?.remove();
  ghost = null;
  pendingLngLat = null;
  pinForm.hidden = true;
  pinError.hidden = true;
}

function setPinMode(on: boolean): void {
  pinMode = on;
  // Feature click handlers stand down while armed (shared registry —
  // streams/POIs/lands/trails check mapClicksClaimed()).
  if (on) claimMapClicks("pin");
  else releaseMapClicks("pin");
  dropBtn.classList.toggle("active", on);
  map.getCanvas().style.cursor = on ? "crosshair" : "";
  setHintVisible(on);
  if (!on) discardPlacement();
}

dropBtn.onclick = () => setPinMode(!pinMode);

map.on("click", (e) => {
  if (!pinMode) return;
  pendingLngLat = e.lngLat;
  if (!ghost) {
    // Fresh placement: drop a draggable ghost right where the user
    // clicked so the spot is visible while they write the note.
    const el = document.createElement("div");
    el.className = "bl-pin bl-pin--ghost";
    el.innerHTML = '<div class="bl-pin-dot"></div>';
    ghost = new maplibregl.Marker({
      element: el,
      anchor: "bottom",
      draggable: true,
    })
      .setLngLat(e.lngLat)
      .addTo(map);
    ghost.on("dragend", () => {
      if (ghost) pendingLngLat = ghost.getLngLat();
    });
    pinNote.value = "";
  } else {
    // Form already open: this click repositions; keep the typed note.
    ghost.setLngLat(e.lngLat);
  }
  setHintVisible(false); // the ghost is down; the form takes over
  pinError.hidden = true;
  pinForm.hidden = false;
  pinNote.focus();
});

pinCancel.onclick = () => {
  discardPlacement();
  if (pinMode) setHintVisible(true); // still armed; invite another tap
};

// Esc: first press discards an open placement, second disarms pin mode.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !pinMode) return;
  if (!pinForm.hidden) {
    discardPlacement();
    setHintVisible(true);
  } else {
    setPinMode(false);
  }
});

pinSave.onclick = async () => {
  if (!pendingLngLat) return;
  pinSave.disabled = true;
  pinSave.textContent = "Saving…";
  try {
    const res = await fetch("/api/pins", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...DEVICE_HEADER },
      body: JSON.stringify({
        lat: pendingLngLat.lat,
        lon: pendingLngLat.lng,
        note: pinNote.value,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    addPinMarker((await res.json()) as Pin);
    setPinMode(false); // also discards the ghost + closes the form
    showToast("Pin saved", "success");
  } catch {
    // Keep the form (and the note) so the user can retry.
    showError("Couldn't save the pin — check your connection and try again.");
  } finally {
    pinSave.disabled = false;
    pinSave.textContent = "Save pin";
  }
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
