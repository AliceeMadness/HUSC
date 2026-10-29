/**
 * Fixed values shared by every module of the app.
 */

export const PALETTE = [
  "#004c93",
  "#b06a3c",
  "#526fa8",
  "#8b5e83",
  "#6f8038",
  "#b34d5a",
  "#3c8a9e",
  "#9a6a2f",
  "#5f6f5b",
  "#a85c82",
];

export const GROUP_TYPE_ORDER = { expositive: 0, laboratory: 1, seminar: 2, other: 3 };
export const DAY_LABELS = { mon: "Lunes", tue: "Martes", wed: "Miércoles", thu: "Jueves", fri: "Viernes" };
export const DAY_ORDER = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4 };
export const SEMESTER_NAMES = { 1: "primer cuatrimestre", 2: "segundo cuatrimestre" };

// localStorage keys.
export const LS_LAST = "horario:last-v2";
export const LS_THEME = "horario:theme";

export const DEFAULT_EVENT_COLOR = "#7c5cbf";
export const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
export const WEEKDAY_CODES = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
export const DAY_MS = 24 * 60 * 60 * 1000;

// "Próximamente" shows the classes that start within this many minutes.
export const UPCOMING_MINUTES = 60;
// Debounce of the server validation after each change.
export const VALIDATION_DELAY_MS = 170;
