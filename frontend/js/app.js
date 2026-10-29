/**
 * Entry point of the HUSC single-page app: boot and event wiring.
 *
 * Modules, from the bottom up:
 *   constants, state, utils, ui, api   shared building blocks
 *   catalog, storage, selection, weeks the plan in memory and in localStorage
 *   schedule, validation               overlaps (instant) and server checks
 *   render, timetable, events          what is drawn on screen
 *   header, landing, plan, plan-dialog navigation and the matrícula
 *   resolve, share                     "Resolver solapes", sharing, exports
 */

import { api } from "./api.js";
import { findYear, yearSubjectIds } from "./catalog.js";
import { bindEventDialog, deleteEditingEvent, openEventDialog, saveEventForm } from "./events.js";
import { applyTheme, renderSemesterToggle, toggleSemester, toggleTheme } from "./header.js";
import { renderFatal, renderLanding, showLanding, updateHeroCounts } from "./landing.js";
import { bindPlanDialog, openPlanDialog } from "./plan-dialog.js";
import { configurePlan, goHome, newPlan, openPlan, openYear } from "./plan.js";
import { renderEverything, renderUpcoming } from "./render.js";
import { dismissResolve, optimizeTimetable, revertResolve, togglePin } from "./resolve.js";
import { selectedGroupIds, toggleGroupSelection } from "./selection.js";
import { downloadIcs, downloadJson, downloadPdf, loadShared, shareSchedule } from "./share.js";
import { collectElements, els, state } from "./state.js";
import { forgetStalePlan, readSaved, readTheme, savedPlanOptions } from "./storage.js";
import { copyText, toast } from "./ui.js";
import { sameSubjectSet } from "./utils.js";
import { onSelectionChanged, scheduleValidation } from "./validation.js";
import { semesterForToday } from "./weeks.js";

document.addEventListener("DOMContentLoaded", init);

async function init() {
  collectElements();
  const preferredTheme =
    readTheme() || (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(preferredTheme, false);
  renderSemesterToggle();
  bindEvents();
  // "Próximamente" follows the clock: appear an hour before, update the
  // countdown, leave when the class starts.
  window.setInterval(renderUpcoming, 30 * 1000);

  try {
    const [meta, directory] = await Promise.all([api.health(), api.degrees()]);
    state.meta = meta;
    state.term = directory.term;
    state.degrees = directory.degrees;
    state.semester = semesterForToday();
    renderSemesterToggle();
    renderLanding();
    updateHeroCounts();
    await openFromUrl();
  } catch (error) {
    if (error?.status === 422) {
      // Forgotten first, so the landing does not offer to reopen it.
      forgetStalePlan();
      showLanding();
      return;
    }
    renderFatal(error);
  }
}

// The app always opens on the HUSC landing, where the hero button starts a
// plan or goes back to the saved one.  Only links that point at something
// (a shared timetable, a plan or a year) skip it.
async function openFromUrl() {
  const params = new URLSearchParams(window.location.search);
  const shareId = params.get("s");
  if (shareId) {
    await loadShared(shareId);
    return;
  }
  const saved = readSaved();
  const requestedPlan = params.get("plan");
  const requestedYear = params.get("year");
  const planIds = (requestedPlan ?? "").split(",").map((item) => item.trim()).filter(Boolean);
  if (planIds.length) {
    const samePlan = saved?.subjectIds && sameSubjectSet(saved.subjectIds, planIds);
    await openPlan(planIds, saved ? savedPlanOptions(saved, samePlan) : {});
    return;
  }
  if (requestedYear && findYear(requestedYear)) {
    const samePlan = saved?.subjectIds && sameSubjectSet(saved.subjectIds, yearSubjectIds(requestedYear) ?? []);
    await openYear(requestedYear, saved ? savedPlanOptions(saved, samePlan) : {});
    return;
  }
  showLanding();
}

// A year of the landing reopens the saved timetable when it is that year.
async function openLandingYear(button) {
  const yearId = button.dataset.year;
  const saved = readSaved();
  const samePlan = saved && sameSubjectSet(saved.subjectIds, yearSubjectIds(yearId) ?? []);
  // Disabled only while loading, to ignore double clicks; it must be
  // enabled again afterwards or the year can never be opened again.
  button.disabled = true;
  try {
    await openYear(yearId, saved ? savedPlanOptions(saved, samePlan) : {});
  } catch (error) {
    toast(error.message, { type: "error" });
  } finally {
    button.disabled = false;
  }
}

// Ctrl+Z (Cmd+Z) undoes the last "Resolver solapes" while it can be reverted.
function undoShortcut(event) {
  if (!state.lastResolve || event.key.toLowerCase() !== "z" || event.shiftKey) return;
  if (!(event.ctrlKey || event.metaKey)) return;
  if (event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  if (els.workspace.classList.contains("hidden") || document.querySelector("dialog[open]")) return;
  event.preventDefault();
  revertResolve();
}

function bindEvents() {
  // Header and landing.
  els.themeToggle.addEventListener("click", toggleTheme);
  els.semesterToggle.addEventListener("click", toggleSemester);
  els.brandLink.addEventListener("click", (event) => {
    event.preventDefault();
    if (state.planSubjectIds.length) goHome();
    else window.scrollTo({ top: 0, behavior: "smooth" });
  });
  els.degreeSearch.addEventListener("input", renderLanding);
  els.degreeGrid.addEventListener("click", (event) => {
    const button = event.target.closest("[data-year]");
    if (button && !button.disabled) openLandingYear(button);
  });
  els.changeYear.addEventListener("click", goHome);
  els.configurePlan.addEventListener("click", configurePlan);
  els.customPlan.addEventListener("click", newPlan);

  // Matrícula.
  els.editPlan.addEventListener("click", openPlanDialog);
  bindPlanDialog();

  // Groups, timetable and alerts.
  els.subjects.addEventListener("click", (event) => {
    const pill = event.target.closest("[data-group-id]");
    if (!pill) return;
    const { subjectId, type, groupId } = pill.dataset;
    toggleGroupSelection(subjectId, type, groupId);
    state.autoResult = null;
    onSelectionChanged();
  });
  els.timetable.addEventListener("click", (event) => {
    const eventBlock = event.target.closest("[data-event-id]");
    if (eventBlock) {
      openEventDialog(eventBlock.dataset.eventId);
      return;
    }
    // A click on a class fixes or frees its group for "Resolver solapes".
    const block = event.target.closest("[data-session-id]");
    if (block) togglePin(block.dataset.groupId);
  });
  // The "Resolver solapes" buttons are re-rendered with the alerts.
  els.alerts.addEventListener("click", (event) => {
    if (event.target.closest("[data-action='resolve']")) optimizeTimetable();
    else if (event.target.closest("[data-action='revert']")) revertResolve();
    else if (event.target.closest("[data-action='dismiss-resolve']")) dismissResolve();
  });
  document.addEventListener("keydown", undoShortcut);
  els.weekSelect.addEventListener("change", () => {
    state.weekId = els.weekSelect.value || null;
    state.weekAuto = false;
    renderEverything();
    scheduleValidation();
  });

  // Extracurricular activities.
  els.addEventBtn.addEventListener("click", () => openEventDialog(null));
  els.eventList.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-event-id]");
    if (chip) openEventDialog(chip.dataset.eventId);
  });
  els.eventForm.addEventListener("submit", saveEventForm);
  els.eventDelete.addEventListener("click", deleteEditingEvent);
  bindEventDialog();

  // Export and share.
  els.exportBtn.addEventListener("click", () => {
    if (!selectedGroupIds().length) {
      toast("Elige algún grupo antes de exportar.", { type: "warn" });
      return;
    }
    els.exportDialog.showModal();
  });
  els.printBtn.addEventListener("click", () => window.print());
  els.exportPdf.addEventListener("click", () => {
    els.exportDialog.close();
    downloadPdf();
  });
  els.exportJson.addEventListener("click", downloadJson);
  els.exportIcs.addEventListener("click", downloadIcs);
  els.shareBtn.addEventListener("click", shareSchedule);
  els.copyShare.addEventListener("click", () => copyText(els.shareUrl.value));

  // Dialogs close with their buttons and with a click on the backdrop.
  for (const button of document.querySelectorAll("[data-close-dialog]")) {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  }
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }
}
