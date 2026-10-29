/**
 * The plan (matrícula) on screen: opening it, the workspace header and URL,
 * and the landing buttons that go back to it or start a new one.
 */

import { api } from "./api.js";
import { yearSubjectIds } from "./catalog.js";
import { SEMESTER_NAMES } from "./constants.js";
import { normalizeIncomingEvents } from "./events.js";
import { setSemester, showWorkspaceControls } from "./header.js";
import { renderConfigureButton, showLanding } from "./landing.js";
import { openPlanDialog } from "./plan-dialog.js";
import { renderEverything } from "./render.js";
import { buildIndexes, prunePins, semesterSubjects, setGroupSelection } from "./selection.js";
import { els, state } from "./state.js";
import { forgetSaved, forgetStalePlan, readSaved, saveLast, savedPlanOptions } from "./storage.js";
import { toast } from "./ui.js";
import { plural } from "./utils.js";
import { scheduleValidation } from "./validation.js";

export async function openYear(yearId, options = {}) {
  const ids = yearSubjectIds(yearId);
  if (ids?.length) return openPlan(ids, options);
  const data = await api.planForYear(yearId);
  return openPlan(data.subject_ids, options);
}

// Load a plan from the API and show its timetable.  ``groupIds``,
// ``customEvents`` and ``pinnedGroupIds`` restore a saved or shared one
// (``undefined`` keeps what is in memory); ``preserveSelection`` keeps the
// groups already chosen for subjects that stay in the plan.
export async function openPlan(
  subjectIds,
  {
    groupIds = [],
    autoResult = null,
    customEvents = undefined,
    pinnedGroupIds = undefined,
    preserveSelection = false,
  } = {},
) {
  const ids = [...new Set(subjectIds)];
  if (!ids.length) throw new Error("Selecciona al menos una asignatura");
  const data = await api.plan(ids);
  const previous = preserveSelection ? state.selected : {};

  state.yearData = data;
  state.term = data.term;
  state.planSubjectIds = data.subjects.map((subject) => subject.id);
  state.yearId = data.years.length === 1 ? data.years[0].id : null;
  state.shareId = null;
  state.autoResult = autoResult;

  buildIndexes(data);
  state.selected = {};
  state.validation = null;

  // Keep already-chosen groups whose subject is still in the new plan.
  for (const [subjectId, types] of Object.entries(previous)) {
    if (!state.planSubjectIds.includes(subjectId)) continue;
    for (const [type, chosen] of Object.entries(types)) {
      for (const groupId of chosen) {
        const group = state.groupIndex.get(groupId);
        if (group && group.subject_id === subjectId && group.type === type) setGroupSelection(groupId);
      }
    }
  }
  for (const groupId of groupIds) setGroupSelection(groupId);
  if (customEvents !== undefined) state.customEvents = normalizeIncomingEvents(customEvents);
  if (pinnedGroupIds !== undefined) state.pinnedGroupIds = new Set(pinnedGroupIds);
  prunePins();

  // Land on a term that actually has subjects in this plan.
  const semesters = new Set(state.subjects.map((subject) => subject.semester ?? 1));
  if (!semesters.has(state.semester) && semesters.size) setSemester([...semesters][0], { announce: false });

  updatePlanHeader();
  showWorkspace();
  renderEverything();
  saveLast();
  scheduleValidation();
}

// Swap the landing for the timetable of the plan already in memory.
export function showWorkspace() {
  showWorkspaceControls(true);
  els.landing.classList.add("hidden");
  els.workspace.classList.remove("hidden");
  updatePlanUrl();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// "Nueva matrícula" forgets the plan entirely: subjects, chosen and pinned
// groups and extracurricular activities, in memory and in this browser.
function resetPlan() {
  Object.assign(state, {
    lastResolve: null,
    planSubjectIds: [],
    customEvents: [],
    editingEventId: null,
    planDraft: new Set(),
    selected: {},
    pinnedGroupIds: new Set(),
    yearData: null,
    yearId: null,
    subjects: [],
    groupIndex: new Map(),
    sessionById: new Map(),
    autoResult: null,
    shareId: null,
    validation: null,
    weekAuto: true,
  });
  forgetSaved();
}

export function newPlan() {
  resetPlan();
  renderConfigureButton();
  openPlanDialog();
}

// "Volver al Inicio" keeps the timetable; the landing offers to go back to it.
export function goHome() {
  showLanding();
}

// The hero button: back to the timetable in memory or saved in this browser,
// or "Editar matrícula" when there is none.
export async function configurePlan() {
  if (state.planSubjectIds.length) {
    showWorkspace();
    // The week shown follows today again, as when the app is opened.
    state.weekAuto = true;
    renderEverything();
    return;
  }
  const saved = readSaved();
  if (!saved) {
    openPlanDialog();
    return;
  }
  els.configurePlan.disabled = true;
  try {
    await openPlan(saved.subjectIds, savedPlanOptions(saved));
  } catch (error) {
    if (error?.status === 422) {
      forgetStalePlan();
      renderConfigureButton();
      return;
    }
    toast(`No se pudo abrir tu horario: ${error.message}`, { type: "error" });
  } finally {
    els.configurePlan.disabled = false;
  }
}

/* --------------------------------------------------------------------------
   Header of the workspace and URL
   -------------------------------------------------------------------------- */

export function updatePlanHeader() {
  const data = state.yearData;
  if (!data) return;
  const singleYear = data.years.length === 1 ? data.years[0] : null;
  const termCount = semesterSubjects().length;
  const term = SEMESTER_NAMES[state.semester];
  // The title is always "Mi horario"; the year and degree go underneath.
  els.yearTitle.textContent = "Mi horario";
  els.yearSubtitle.textContent = singleYear
    ? `${singleYear.name} · ${singleYear.degree_name} · ${plural(termCount, "asignatura", "asignaturas")} en el ${term}`
    : `${plural(termCount, "materia", "materias")} en el ${term} · ${plural(data.years.length, "curso", "cursos")} · ${plural(data.degrees.length, "grado", "grados")}`;
  els.yearChip.textContent = planChipLabel();
  els.yearChip.title = planLabel();
}

function distinctDegreeNames(useShortNames) {
  const names = [
    ...new Set((state.yearData?.degrees ?? []).map((degree) => (useShortNames ? degree.short_name : degree.name))),
  ];
  // "Matemáticas" is redundant when the plan also contains "Matemáticas + Física".
  return names.filter((name, index) => !names.some((other, otherIndex) => otherIndex !== index && other.includes(name)));
}

export function planChipLabel() {
  const data = state.yearData;
  if (!data) return "";
  if (data.years.length === 1) {
    const year = data.years[0];
    return `${year.degree_short_name} · ${year.number}º`;
  }
  if (data.degrees.length === 1) {
    const numbers = data.years.map((year) => `${year.number}º`).join(" + ");
    return `${data.degrees[0].short_name} · ${numbers}`;
  }
  return `${distinctDegreeNames(true).join(" + ")} · ${plural(data.subjects.length, "materia", "materias")}`;
}

export function planLabel() {
  const data = state.yearData;
  if (!data) return "Horario";
  if (data.years.length === 1) {
    const year = data.years[0];
    return `${year.degree_name} · ${year.name}`;
  }
  return `${distinctDegreeNames(false).join(" + ")} · ${plural(data.subjects.length, "materia", "materias")}`;
}

// ``?year=`` for a whole year, ``?plan=`` for any other mix of subjects.
export function updatePlanUrl() {
  if (!state.planSubjectIds.length) return;
  const url = new URL(window.location.href);
  const years = new Set(state.subjects.map((subject) => subject.year_id));
  const yearId = years.size === 1 ? [...years][0] : null;
  const fullYear = yearId ? yearSubjectIds(yearId) : null;
  const isFullYear =
    fullYear &&
    fullYear.length === state.planSubjectIds.length &&
    fullYear.every((subjectId) => state.planSubjectIds.includes(subjectId));
  url.search = isFullYear
    ? `?year=${encodeURIComponent(yearId)}`
    : `?plan=${state.planSubjectIds.map(encodeURIComponent).join(",")}`;
  window.history.replaceState({}, "", url);
}
