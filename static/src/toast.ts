/**
 * Minimal toast/snackbar. One bottom-centered stack (`.bl-toasts`,
 * z-indexed by --z-toast); each toast slides in, auto-dismisses, and
 * removes itself from the DOM. No queueing/dedupe — callers fire and
 * forget. Dependency-free so any module can import it without cycles.
 */

type ToastKind = "success" | "error" | "info";

let container: HTMLElement | null = null;

function ensureContainer(): HTMLElement {
  if (!container) {
    container = document.createElement("div");
    container.className = "bl-toasts";
    document.body.appendChild(container);
  }
  return container;
}

export function showToast(
  message: string,
  kind: ToastKind = "info",
  durationMs = 3500,
): void {
  const el = document.createElement("div");
  el.className = `bl-toast bl-toast--${kind}`;
  el.setAttribute("role", "status");
  el.textContent = message;
  ensureContainer().appendChild(el);
  // Two frames so the initial (hidden) styles commit before the
  // transition class lands; rAF-in-rAF is the reliable form.
  requestAnimationFrame(() =>
    requestAnimationFrame(() => el.classList.add("is-in")),
  );
  window.setTimeout(() => {
    el.classList.remove("is-in");
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    // Fallback removal in case transitions are disabled (reduced motion).
    window.setTimeout(() => el.remove(), 400);
  }, durationMs);
}
