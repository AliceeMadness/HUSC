/**
 * Small browser-side services: toasts, clipboard and file downloads.
 */

import { els } from "./state.js";

export function toast(message, { type = "info", duration = 4200 } = {}) {
  if (!els.toast) return;
  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " is-error" : type === "warn" ? " is-warn" : ""}`;
  node.textContent = message;
  els.toast.appendChild(node);
  while (els.toast.children.length > 3) els.toast.firstElementChild.remove();
  window.setTimeout(() => node.remove(), duration);
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copiado al portapapeles.");
    return true;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch {
      copied = false;
    }
    textarea.remove();
    toast(copied ? "Copiado al portapapeles." : "No se pudo copiar automáticamente.", {
      type: copied ? "info" : "error",
    });
    return copied;
  }
}

export function downloadBlob(content, filename, type) {
  const blob = content instanceof Blob ? content : new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
