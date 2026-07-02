/**
 * Styled confirm dialog replacing native confirm(), which drops an
 * OS-chrome dialog into an otherwise fully custom UI. Builds one
 * `.modal` lazily with the same markup contract as the static modals in
 * index.html (backdrop, card, actions) and resolves a promise with the
 * user's choice. Dependency-free so any module can import it without
 * cycles.
 */

export interface ConfirmOptions {
  title: string;
  message: string;
  /** Label for the affirmative button (default "Confirm"). */
  confirmLabel?: string;
  cancelLabel?: string;
  /** Style the affirmative button as destructive (red). */
  danger?: boolean;
}

let modal: HTMLDivElement | null = null;
let titleEl: HTMLHeadingElement;
let msgEl: HTMLParagraphElement;
let okBtn: HTMLButtonElement;
let cancelBtn: HTMLButtonElement;
let resolveOpen: ((v: boolean) => void) | null = null;

function settle(v: boolean): void {
  if (modal) modal.hidden = true;
  const r = resolveOpen;
  resolveOpen = null;
  r?.(v);
}

function ensureModal(): void {
  if (modal) return;
  modal = document.createElement("div");
  modal.className = "modal";
  modal.hidden = true;
  modal.innerHTML =
    '<div class="modal-backdrop"></div>' +
    '<div class="modal-card" role="alertdialog" aria-modal="true">' +
    "<h2></h2>" +
    '<p class="modal-lede"></p>' +
    '<div class="modal-actions">' +
    '<button type="button" class="bl-confirm-ok"></button>' +
    '<button type="button" class="secondary bl-confirm-cancel"></button>' +
    "</div></div>";
  document.body.appendChild(modal);
  titleEl = modal.querySelector("h2") as HTMLHeadingElement;
  msgEl = modal.querySelector(".modal-lede") as HTMLParagraphElement;
  okBtn = modal.querySelector(".bl-confirm-ok") as HTMLButtonElement;
  cancelBtn = modal.querySelector(".bl-confirm-cancel") as HTMLButtonElement;
  okBtn.onclick = () => settle(true);
  cancelBtn.onclick = () => settle(false);
  (modal.querySelector(".modal-backdrop") as HTMLElement).onclick = () =>
    settle(false);
  document.addEventListener("keydown", (e) => {
    if (modal && !modal.hidden && e.key === "Escape") settle(false);
  });
}

export function confirmDialog(opts: ConfirmOptions): Promise<boolean> {
  ensureModal();
  settle(false); // a second call supersedes any dialog still open
  titleEl.textContent = opts.title;
  msgEl.textContent = opts.message;
  okBtn.textContent = opts.confirmLabel ?? "Confirm";
  okBtn.classList.toggle("danger", !!opts.danger);
  cancelBtn.textContent = opts.cancelLabel ?? "Cancel";
  modal!.hidden = false;
  return new Promise((resolve) => {
    resolveOpen = resolve;
    cancelBtn.focus(); // safe default for destructive confirms
  });
}
