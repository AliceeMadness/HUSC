/**
 * Reaction to selection changes and the debounced server validation.
 */

import { api } from "./api.js";
import { VALIDATION_DELAY_MS } from "./constants.js";
import { updatePlanUrl } from "./plan.js";
import { renderAlerts, renderEverything, renderStatus } from "./render.js";
import { prunePins, selectedGroupIds, semesterSubjectIds } from "./selection.js";
import { state } from "./state.js";
import { saveLast } from "./storage.js";
import { toast } from "./ui.js";
import { activeWeekId } from "./weeks.js";

// After any manual change of the chosen groups.
export function onSelectionChanged() {
  state.lastResolve = null;
  state.validation = null;
  prunePins();
  renderEverything();
  saveLast();
  scheduleValidation();

  // The timetable no longer matches the shared link it came from.
  if (state.shareId) {
    state.shareId = null;
    updatePlanUrl();
  }
}

export function scheduleValidation() {
  window.clearTimeout(state.validationTimer);
  state.validationTimer = window.setTimeout(refreshValidation, VALIDATION_DELAY_MS);
}

const validationKey = () => `${selectedGroupIds().join(",")}@${activeWeekId() ?? ""}`;

async function refreshValidation() {
  const ids = selectedGroupIds();
  const week = activeWeekId();
  const key = validationKey();
  const token = ++state.validationToken;
  if (!ids.length) {
    state.validation = null;
    renderAlerts();
    renderStatus();
    return;
  }
  try {
    const result = await api.validate({
      group_ids: ids,
      subject_ids: semesterSubjectIds(),
      custom_events: state.customEvents,
      min_free_minutes: 30,
      week,
    });
    // Ignore answers overtaken by a newer change.
    if (token !== state.validationToken || key !== validationKey()) return;
    state.validation = result;
    renderAlerts();
    renderStatus();
  } catch (error) {
    if (token !== state.validationToken) return;
    toast(`No se pudo validar el horario: ${error.message}`, { type: "error" });
  }
}
