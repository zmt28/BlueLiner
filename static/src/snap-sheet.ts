/**
 * Bottom-sheet snap-state wiring (extracted in PR B1f). Shared
 * between the river-detail panel and the unified controls panel.
 *
 * Handlers attached when invoked:
 *   - pointer drag on the grip: follow finger, snap to peek/full on
 *     release; drag past CLOSE_THRESHOLD_PX while at peek dismisses
 *     (calls opts.onClose).
 *   - tap on the body (not on a control) while at peek: expand to full.
 *   - swipe on the body at peek: up -> full, down -> close.
 *   - if tabSelector is provided, a tab click at peek auto-expands
 *     to full (river panel + controls panel both use this).
 *
 * All handlers gate on `window.matchMedia("(max-width: 700px)")` so
 * desktop calls are no-ops; the panels' desktop CSS handles
 * positioning without snap classes.
 *
 * Window-bridged because the legacy app.js's controls-panel init
 * still calls wireSnapSheet directly. Once controls.ts (PR B1i)
 * extracts that wiring, the window line drops.
 */

interface SnapSheetOpts {
  cardSelector: string;
  gripSelector: string;
  bodySelector: string;
  tabSelector?: string;
  onClose: () => void;
}

interface SnapSheetController {
  setSnap: (state: "peek" | "full") => void;
}

export function wireSnapSheet(
  panel: HTMLElement | null,
  opts: SnapSheetOpts,
): SnapSheetController | null {
  if (!panel) return null;
  const card = panel.querySelector<HTMLElement>(opts.cardSelector);
  const grip = panel.querySelector<HTMLElement>(opts.gripSelector);
  const body = panel.querySelector<HTMLElement>(opts.bodySelector);
  if (!card || !grip || !body) return null;
  const onClose = opts.onClose;
  const tabSelector = opts.tabSelector || null;

  let drag: { startY: number; lastY: number; baseTranslate: number } | null = null;
  const CLOSE_THRESHOLD_PX = 110;
  const SWIPE_THRESHOLD = 36;

  function cardHeight(): number {
    return card!.getBoundingClientRect().height;
  }
  function isMobile(): boolean {
    return window.matchMedia("(max-width: 700px)").matches;
  }
  function setSnap(state: "peek" | "full"): void {
    panel!.classList.remove("peek", "full");
    panel!.classList.add(state);
  }

  function onDown(e: PointerEvent): void {
    if (!isMobile()) return;
    drag = {
      startY: e.clientY,
      lastY: e.clientY,
      // 62% translate at peek matches the CSS; full is 0.
      baseTranslate: panel!.classList.contains("peek") ? 0.62 : 0,
    };
    card!.classList.add("dragging");
    // setPointerCapture is standard on HTMLElement but the inline
    // guard mirrors the legacy code; harmless null-check.
    grip!.setPointerCapture(e.pointerId);
  }
  function onMove(e: PointerEvent): void {
    if (!drag) return;
    const dy = e.clientY - drag.startY;
    drag.lastY = e.clientY;
    const h = cardHeight();
    let px = drag.baseTranslate * h + dy;
    if (px < 0) px = 0;
    card!.style.transform = `translateY(${px}px)`;
  }
  function onUp(): void {
    if (!drag) return;
    const dy = drag.lastY - drag.startY;
    const startedAtPeek = drag.baseTranslate > 0;
    card!.style.transform = "";
    card!.classList.remove("dragging");
    drag = null;
    if (startedAtPeek && dy > CLOSE_THRESHOLD_PX) {
      onClose();
      return;
    }
    if (!startedAtPeek && dy > CLOSE_THRESHOLD_PX * 1.4) {
      setSnap("peek");
      return;
    }
    if (dy < -30) setSnap("full");
    else if (dy > 30) setSnap("peek");
    else setSnap(startedAtPeek ? "full" : "peek");
  }
  grip.addEventListener("pointerdown", onDown);
  grip.addEventListener("pointermove", onMove);
  grip.addEventListener("pointerup", onUp);
  grip.addEventListener("pointercancel", onUp);

  body.addEventListener("click", (e) => {
    if (!isMobile()) return;
    if (!panel.classList.contains("peek")) return;
    if ((e.target as HTMLElement).closest("button, a, label, input, summary, select"))
      return;
    setSnap("full");
  });

  let bodyDrag: { startY: number; lastY?: number } | null = null;
  body.addEventListener("pointerdown", (e) => {
    if (!isMobile()) return;
    if (!panel.classList.contains("peek")) return;
    if ((e.target as HTMLElement).closest("button, a, label, input, summary, select"))
      return;
    bodyDrag = { startY: e.clientY };
  });
  body.addEventListener("pointermove", (e) => {
    if (!bodyDrag) return;
    bodyDrag.lastY = e.clientY;
  });
  body.addEventListener("pointerup", () => {
    if (!bodyDrag) return;
    const dy = (bodyDrag.lastY ?? bodyDrag.startY) - bodyDrag.startY;
    bodyDrag = null;
    if (Math.abs(dy) < SWIPE_THRESHOLD) return;
    if (dy < 0) setSnap("full");
    else onClose();
  });
  body.addEventListener("pointercancel", () => {
    bodyDrag = null;
  });

  if (tabSelector) {
    body.addEventListener("click", (e) => {
      if (!isMobile()) return;
      const tab = (e.target as HTMLElement).closest(tabSelector);
      if (!tab) return;
      if (panel.classList.contains("peek")) setSnap("full");
    });
  }

  return { setSnap };
}

// -- Window bridge for legacy app.js -----------------------------------

declare global {
  interface Window {
    wireSnapSheet: typeof wireSnapSheet;
  }
}

window.wireSnapSheet = wireSnapSheet;
