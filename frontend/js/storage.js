/**
 * The last timetable and the theme, kept in this browser's localStorage.
 *
 * Every access is wrapped in try/catch: storage can be unavailable (private
 * mode, blocked site data) and the app must keep working without it.
 */

import { yearSubjectIds } from "./catalog.js";
import { LS_LAST, LS_THEME } from "./constants.js";
import { allSelectedGroupIds } from "./selection.js";
import { state } from "./state.js";
import { toast } from "./ui.js";

const strings = (value) => (Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);

// The saved timetable, validated and sanitised, or null.
export function readSaved() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_LAST) || "null");
    if (!parsed) return null;
    let subjectIds = strings(parsed.planSubjectIds);
    if (!subjectIds.length && typeof parsed.yearId === "string") {
      subjectIds = yearSubjectIds(parsed.yearId) ?? [];
    }
    if (!subjectIds.length) return null;
    const auto =
      parsed.autoResult &&
      ["partial", "relaxed"].includes(parsed.autoResult.status) &&
      Array.isArray(parsed.autoResult.excluded_subjects) &&
      Array.isArray(parsed.autoResult.requested_subject_ids)
        ? parsed.autoResult
        : null;
    return {
      subjectIds,
      groupIds: strings(parsed.groupIds),
      customEvents: Array.isArray(parsed.customEvents) ? parsed.customEvents : [],
      pinnedGroupIds: strings(parsed.pinnedGroupIds),
      autoResult: auto,
    };
  } catch {
    return null;
  }
}

// ``openPlan`` options that restore a saved timetable.  When the plan being
// opened is not the saved one (``samePlan`` false) nothing is restored and
// the extracurricular activities and pins in memory are kept.
export function savedPlanOptions(saved, samePlan = true) {
  return {
    groupIds: samePlan ? saved.groupIds ?? [] : [],
    autoResult: samePlan ? saved.autoResult ?? null : null,
    customEvents: samePlan ? saved.customEvents ?? [] : undefined,
    pinnedGroupIds: samePlan ? saved.pinnedGroupIds ?? [] : undefined,
    preserveSelection: false,
  };
}

export function saveLast() {
  if (!state.planSubjectIds.length) return;
  try {
    const autoResult = state.autoResult
      ? {
          status: state.autoResult.status,
          requested_subject_ids: state.autoResult.requested_subject_ids,
          excluded_subjects: state.autoResult.excluded_subjects,
        }
      : null;
    localStorage.setItem(
      LS_LAST,
      JSON.stringify({
        planSubjectIds: state.planSubjectIds,
        yearId: state.yearId,
        groupIds: allSelectedGroupIds(),
        customEvents: state.customEvents,
        pinnedGroupIds: [...state.pinnedGroupIds],
        autoResult,
        savedAt: new Date().toISOString(),
      }),
    );
  } catch {
    /* storage can be unavailable in private mode */
  }
}

export function forgetSaved() {
  try {
    localStorage.removeItem(LS_LAST);
  } catch {
    /* storage can be unavailable in private mode */
  }
}

// A catalogue refresh can invalidate subject ids saved in the browser.
export function forgetStalePlan() {
  forgetSaved();
  toast("La matrícula guardada ya no existe en el catálogo actual. Elige las materias de nuevo.", {
    type: "warn",
    duration: 6000,
  });
}

export function readTheme() {
  try {
    return localStorage.getItem(LS_THEME);
  } catch {
    return null;
  }
}

export function saveTheme(theme) {
  try {
    localStorage.setItem(LS_THEME, theme);
  } catch {
    /* ignore */
  }
}
