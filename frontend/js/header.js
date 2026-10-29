/**
 * Header controls: light/dark theme and the term (cuatrimestre) on screen.
 */

import { SEMESTER_NAMES } from "./constants.js";
import { updatePlanHeader } from "./plan.js";
import { renderEverything } from "./render.js";
import { els, state } from "./state.js";
import { saveTheme } from "./storage.js";
import { toast } from "./ui.js";
import { scheduleValidation } from "./validation.js";

export function applyTheme(theme, persist = true) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = value;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = value === "dark" ? "#0a0f1e" : "#f3f5f9";
  if (els.themeToggle) {
    els.themeToggle.setAttribute("aria-label", value === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro");
  }
  if (persist) saveTheme(value);
}

export function toggleTheme() {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
}

export function renderSemesterToggle() {
  if (!els.semesterToggle) return;
  const other = state.semester === 1 ? 2 : 1;
  els.semesterLabel.textContent = `${state.semester}º Cuatrimestre`;
  els.semesterToggle.setAttribute("aria-label", `Cambiar al ${SEMESTER_NAMES[other]}`);
  els.semesterToggle.title = `Viendo el ${SEMESTER_NAMES[state.semester]}. Pulsa para ver el ${SEMESTER_NAMES[other]}.`;
}

// A manual switch lasts for the visit; the next one starts on today's term.
export function setSemester(semester, { announce = true } = {}) {
  state.semester = semester === 2 ? 2 : 1;
  renderSemesterToggle();
  // Each term has its own weeks: go back to the one matching today.
  state.weekAuto = true;
  if (!state.planSubjectIds.length) return;
  state.validation = null;
  updatePlanHeader();
  renderEverything();
  scheduleValidation();
  if (announce) toast(`Mostrando el ${SEMESTER_NAMES[state.semester]}.`);
}

export function toggleSemester() {
  setSemester(state.semester === 1 ? 2 : 1);
}

// Header buttons that only make sense with a timetable open.
export function showWorkspaceControls(visible) {
  for (const element of [els.yearChip, els.shareBtn, els.semesterToggle]) {
    element.classList.toggle("hidden", !visible);
  }
  // The theme can be changed everywhere, also on the landing.
  els.themeToggle.classList.remove("hidden");
}
