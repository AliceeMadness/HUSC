/**
 * Pure helpers: formatting, text normalisation and validation of values.
 */

import { DEFAULT_EVENT_COLOR } from "./constants.js";

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

export function plural(count, singular, pluralForm) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

export function formatHours(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function minutesToHm(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

// Lower case without accents, for searches.
export function normalizeSearch(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

export function slugify(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function isTime(value) {
  return typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

export function safeEventColor(value) {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value) ? value : DEFAULT_EVENT_COLOR;
}

export function sameSubjectSet(first, second) {
  return first.length === second.length && first.every((subjectId) => second.includes(subjectId));
}

export const capitalize = (text) => (text ? text[0].toUpperCase() + text.slice(1) : "");
