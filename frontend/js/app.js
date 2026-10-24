import { api } from "./api.js";
import { DAYS, renderTimetable, toMinutes } from "./timetable.js";

/* --------------------------------------------------------------------------
   Constants and state
   -------------------------------------------------------------------------- */

const PALETTE = [
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
const GROUP_TYPE_ORDER = { expositive: 0, laboratory: 1, seminar: 2, other: 3 };
const DAY_LABELS = { mon: "Lunes", tue: "Martes", wed: "Miércoles", thu: "Jueves", fri: "Viernes" };
const DAY_ORDER = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4 };
const LS_LAST = "horario:last-v2";
const LS_THEME = "horario:theme";
const SEMESTER_NAMES = { 1: "primer cuatrimestre", 2: "segundo cuatrimestre" };

const state = {
  meta: null,
  degrees: [],
  term: null,
  yearData: null,
  yearId: null,
  planSubjectIds: [],
  planDraft: new Set(),
  customEvents: [],
  pinnedGroupIds: new Set(),
  editingEventId: null,
  shareId: null,
  subjects: [],
  groupIndex: new Map(),
  allSessions: [],
  sessionById: new Map(),
  selected: {},
  semester: 1,
  // ISO week on screen (``2025-W43``).  While ``weekAuto`` is true it follows
  // the device date; picking a week in the menu turns it off.
  weekId: null,
  weekAuto: true,
  localSchedule: { sessions: [], conflicts: [] },
  validation: null,
  validationKey: null,
  autoResult: null,
  // "Resolver solapes" is running (its button lives in the overlap alert).
  resolving: false,
  validationTimer: null,
  validationToken: 0,
};

let els = {};

/* --------------------------------------------------------------------------
   Boot
   -------------------------------------------------------------------------- */

document.addEventListener("DOMContentLoaded", init);

async function init() {
  els = {
    landing: document.getElementById("landing"),
    workspace: document.getElementById("workspace"),
    degreeGrid: document.getElementById("degree-grid"),
    yearChip: document.getElementById("year-chip"),
    themeToggle: document.getElementById("theme-toggle"),
    semesterToggle: document.getElementById("semester-toggle"),
    semesterLabel: document.getElementById("semester-label"),
    shareBtn: document.getElementById("share-btn"),
    brandLink: document.getElementById("brand-link"),
    changeYear: document.getElementById("change-year"),
    customPlan: document.getElementById("custom-plan"),
    resumePlan: document.getElementById("resume-plan"),
    resumePlanLabel: document.getElementById("resume-plan-label"),
    editPlan: document.getElementById("edit-plan"),
    addEventBtn: document.getElementById("add-event-btn"),
    weekPicker: document.getElementById("week-picker"),
    weekSelect: document.getElementById("week-select"),
    eventList: document.getElementById("event-list"),
    eventDialog: document.getElementById("event-dialog"),
    eventDialogTitle: document.getElementById("event-dialog-title"),
    eventForm: document.getElementById("event-form"),
    eventTitle: document.getElementById("event-title"),
    eventDay: document.getElementById("event-day"),
    eventStart: document.getElementById("event-start"),
    eventEnd: document.getElementById("event-end"),
    eventLocation: document.getElementById("event-location"),
    eventDelete: document.getElementById("event-delete"),
    yearTitle: document.getElementById("year-title"),
    yearSubtitle: document.getElementById("year-subtitle"),
    subjects: document.getElementById("subjects"),
    selectionCount: document.getElementById("selection-count"),
    conflictPill: document.getElementById("conflict-pill"),
    hoursStat: document.getElementById("hours-stat"),
    daysStat: document.getElementById("days-stat"),
    alerts: document.getElementById("alerts"),
    timetable: document.getElementById("timetable"),
    exportBtn: document.getElementById("export-btn"),
    printBtn: document.getElementById("print-btn"),
    shareDialog: document.getElementById("share-dialog"),
    shareUrl: document.getElementById("share-url"),
    shareNote: document.getElementById("share-note"),
    copyShare: document.getElementById("copy-share"),
    exportDialog: document.getElementById("export-dialog"),
    planDialog: document.getElementById("plan-dialog"),
    planSearch: document.getElementById("plan-search"),
    planOptions: document.getElementById("plan-options"),
    planCount: document.getElementById("plan-count"),
    planClear: document.getElementById("plan-clear"),
    planApply: document.getElementById("plan-apply"),
    toast: document.getElementById("toast"),
  };

  const preferredTheme =
    localStorage.getItem(LS_THEME) ||
    (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(preferredTheme, false);
  renderSemesterToggle();
  bindEvents();

  try {
    const [meta, directory] = await Promise.all([api.health(), api.degrees()]);
    state.meta = meta;
    state.term = directory.term;
    state.degrees = directory.degrees;
    state.semester = semesterForToday();
    renderSemesterToggle();
    renderLanding();
    updateHeroCounts();

    const params = new URLSearchParams(window.location.search);
    const shareId = params.get("s");
    if (shareId) {
      await loadShared(shareId);
      return;
    }

    const saved = readSaved();
    const requestedPlan = params.get("plan");
    const requestedYear = params.get("year");
    if (requestedPlan) {
      const ids = requestedPlan.split(",").map((item) => item.trim()).filter(Boolean);
      if (ids.length) {
        const samePlan = saved?.subjectIds && sameSubjectSet(saved.subjectIds, ids);
        await openPlan(ids, {
          groupIds: samePlan ? saved.groupIds ?? [] : [],
          autoResult: samePlan ? saved.autoResult ?? null : null,
          customEvents: samePlan ? saved.customEvents ?? [] : undefined,
          pinnedGroupIds: samePlan ? saved.pinnedGroupIds ?? [] : undefined,
          preserveSelection: false,
        });
        return;
      }
    }
    if (requestedYear && findYear(requestedYear)) {
      const fullYearIds = yearSubjectIds(requestedYear) ?? [];
      const samePlan = saved?.subjectIds && sameSubjectSet(saved.subjectIds, fullYearIds);
      await openYear(requestedYear, {
        groupIds: samePlan ? saved.groupIds ?? [] : [],
        autoResult: samePlan ? saved.autoResult ?? null : null,
        customEvents: samePlan ? saved.customEvents ?? [] : undefined,
        pinnedGroupIds: samePlan ? saved.pinnedGroupIds ?? [] : undefined,
        preserveSelection: false,
      });
      return;
    }
    // The app always opens on the HUSC presentation; a saved timetable is
    // one click away through "Ir a mi horario".  Only links that point
    // at something (a shared timetable, a plan or a year) skip it.
    showLanding();
  } catch (error) {
    if (error?.status === 422) {
      // A catalogue refresh can invalidate subject ids saved in the browser.
      try {
        localStorage.removeItem(LS_LAST);
      } catch {
        /* ignore */
      }
      showLanding();
      toast("La matrícula guardada ya no existe en el catálogo actual. Elige las materias de nuevo.", {
        type: "warn",
        duration: 6000,
      });
      return;
    }
    renderFatal(error);
  }
}

/* --------------------------------------------------------------------------
   Small utilities
   -------------------------------------------------------------------------- */

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function plural(count, singular, pluralForm) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

function formatHours(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} min`;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

function findYear(yearId) {
  for (const degree of state.degrees) {
    const year = degree.years.find((item) => item.id === yearId);
    if (year) return { degree, year };
  }
  return null;
}

function yearSubjectIds(yearId) {
  const found = findYear(yearId);
  return found?.year.subjects?.map((subject) => subject.id) ?? null;
}

function sameSubjectSet(first, second) {
  return (
    first.length === second.length && first.every((subjectId) => second.includes(subjectId))
  );
}

function readSaved() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_LAST) || "null");
    if (!parsed) return null;
    let subjectIds = Array.isArray(parsed.planSubjectIds)
      ? parsed.planSubjectIds.filter((id) => typeof id === "string")
      : [];
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
      groupIds: Array.isArray(parsed.groupIds)
        ? parsed.groupIds.filter((id) => typeof id === "string")
        : [],
      customEvents: Array.isArray(parsed.customEvents) ? parsed.customEvents : [],
      pinnedGroupIds: Array.isArray(parsed.pinnedGroupIds)
        ? parsed.pinnedGroupIds.filter((id) => typeof id === "string")
        : [],
      autoResult: auto,
    };
  } catch {
    return null;
  }
}

function saveLast() {
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

function isTime(value) {
  return typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);
}

function safeEventColor(value) {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value)
    ? value
    : "#7c5cbf";
}

function normalizeIncomingEvents(events) {
  return (Array.isArray(events) ? events : [])
    .filter(
      (event) =>
        event &&
        typeof event.id === "string" &&
        typeof event.title === "string" &&
        event.title.trim() &&
        DAY_ORDER[event.day] !== undefined &&
        isTime(event.start) &&
        isTime(event.end) &&
        toMinutes(event.end) > toMinutes(event.start),
    )
    .map((event) => ({
      ...event,
      color: safeEventColor(event.color),
      kind: "extracurricular",
    }))
    .slice(0, 80);
}

function prunePins() {
  const selected = new Set(allSelectedGroupIds());
  state.pinnedGroupIds = new Set(
    [...state.pinnedGroupIds].filter(
      (groupId) => selected.has(groupId) && state.groupIndex.has(groupId),
    ),
  );
}

function isPinnedGroup(groupId) {
  return state.pinnedGroupIds.has(groupId);
}

/* --------------------------------------------------------------------------
   Extracurricular events
   -------------------------------------------------------------------------- */

function openEventDialog(eventId = null) {
  const event = eventId
    ? state.customEvents.find((item) => item.id === eventId)
    : null;
  const values = event ?? {};
  state.editingEventId = event?.id ?? null;
  els.eventDialogTitle.textContent = event ? "Editar extraescolar" : "Añadir extraescolar";
  els.eventTitle.value = values.title ?? "";
  els.eventDay.value = values.day ?? "mon";
  els.eventStart.value = values.start ?? "18:00";
  els.eventEnd.value = values.end ?? "20:00";
  els.eventLocation.value = values.location ?? "";
  const color = safeEventColor(values.color);
  const colorInput = els.eventDialog.querySelector(
    `input[name="event-color"][value="${color}"]`,
  );
  if (colorInput) colorInput.checked = true;
  else els.eventDialog.querySelector('input[name="event-color"]').checked = true;
  els.eventDelete.classList.toggle("hidden", !event);
  els.eventDialog.showModal();
  window.setTimeout(() => els.eventTitle.focus(), 50);
}

function saveEventForm(event) {
  event.preventDefault();
  const title = els.eventTitle.value.trim();
  const start = els.eventStart.value;
  const end = els.eventEnd.value;
  if (!title) {
    toast("Ponle un título a la extraescolar.", { type: "warn" });
    return;
  }
  if (!isTime(start) || !isTime(end) || toMinutes(end) <= toMinutes(start)) {
    toast("El horario de la extraescolar no es válido.", { type: "warn" });
    return;
  }
  if (
    toMinutes(start) < toMinutes(state.term.day_start) ||
    toMinutes(end) > toMinutes(state.term.day_end)
  ) {
    toast(
      `La extraescolar debe estar entre las ${state.term.day_start} y las ${state.term.day_end}.`,
      { type: "warn" },
    );
    return;
  }
  const selectedColor = els.eventDialog.querySelector(
    'input[name="event-color"]:checked',
  );
  const id =
    state.editingEventId ||
    `evt-${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
  const item = {
    id,
    title,
    day: els.eventDay.value,
    start,
    end,
    location: els.eventLocation.value.trim() || null,
    color: safeEventColor(selectedColor?.value),
    kind: "extracurricular",
  };
  const index = state.customEvents.findIndex((current) => current.id === id);
  if (index === -1) state.customEvents.push(item);
  else state.customEvents[index] = item;
  state.customEvents.sort(
    (a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start),
  );
  state.editingEventId = null;
  els.eventDialog.close();
  state.autoResult = null;
  state.validation = null;
  renderEverything();
  saveLast();
  scheduleValidation();
  toast(index === -1 ? "Extraescolar añadida." : "Extraescolar actualizada.");
}

function deleteEditingEvent() {
  if (!state.editingEventId) return;
  state.customEvents = state.customEvents.filter(
    (event) => event.id !== state.editingEventId,
  );
  state.editingEventId = null;
  els.eventDialog.close();
  state.autoResult = null;
  state.validation = null;
  renderEverything();
  saveLast();
  scheduleValidation();
  toast("Extraescolar eliminada.");
}

function renderEventList() {
  if (!state.customEvents.length) {
    els.eventList.innerHTML = `
      <p class="event-empty">Añade entrenamientos, trabajo o academias con «Extraescolar» para que el horario los respete.</p>`;
    return;
  }
  const chips = state.customEvents
    .map(
      (event) => `
      <button class="event-chip" type="button" data-event-id="${escapeHtml(event.id)}"
        style="--event-color:${safeEventColor(event.color)}"
        title="${escapeHtml(`${event.title} · ${DAY_LABELS[event.day]} ${event.start}–${event.end}${event.location ? ` · ${event.location}` : ""}`)}">
        <span class="event-chip-dot" aria-hidden="true"></span>
        <span>${escapeHtml(event.title)}</span>
        <small>${escapeHtml(DAY_LABELS[event.day].slice(0, 3))} ${event.start}–${event.end}</small>
      </button>`,
    )
    .join("");
  els.eventList.innerHTML = `<div class="event-chips">${chips}</div>`;
}

// Time-of-day preferences were removed from the UI; no slot is avoided.
function blockedSlots() {
  return [];
}

/* --------------------------------------------------------------------------
   Theme
   -------------------------------------------------------------------------- */

function applyTheme(theme, persist = true) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = value;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = value === "dark" ? "#0a0f1e" : "#f3f5f9";
  if (els.themeToggle) {
    els.themeToggle.setAttribute("aria-label", value === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro");
  }
  if (persist) {
    try {
      localStorage.setItem(LS_THEME, value);
    } catch {
      /* ignore */
    }
  }
}

/* --------------------------------------------------------------------------
   Semester (cuatrimestre)
   -------------------------------------------------------------------------- */

// The term of the device date.  With weekly data the second term starts on
// the Monday of its first teaching week (moved to this academic year), so
// January exams still count as the first term; summer belongs to the
// second until August, when the next course begins.
function semesterForToday() {
  const today = todayUtc();
  const firstOfSecond = (state.term?.calendar ?? []).find((week) => week.semester === 2);
  if (firstOfSecond) {
    const academicStart = Date.UTC(academicYearStart(today), 7, 1);
    return today >= weekInCurrentYear(firstOfSecond.id) && today >= academicStart ? 2 : 1;
  }
  const month = today.getUTCMonth();
  return month >= 1 && month <= 6 ? 2 : 1;
}

function renderSemesterToggle() {
  if (!els.semesterToggle) return;
  const other = state.semester === 1 ? 2 : 1;
  els.semesterLabel.textContent = `${state.semester}C`;
  els.semesterToggle.setAttribute("aria-label", `Cambiar al ${SEMESTER_NAMES[other]}`);
  els.semesterToggle.title = `Viendo el ${SEMESTER_NAMES[state.semester]}. Pulsa para ver el ${SEMESTER_NAMES[other]}.`;
}

function setSemester(semester, { announce = true } = {}) {
  // A manual switch lasts for the visit; the next one starts on today's term.
  state.semester = semester === 2 ? 2 : 1;
  renderSemesterToggle();
  // Each term has its own weeks: go back to the one matching today.
  state.weekAuto = true;
  if (!state.planSubjectIds.length) return;
  state.validation = null;
  state.validationKey = null;
  updatePlanHeader();
  renderEverything();
  scheduleValidation();
  if (announce) toast(`Mostrando el ${SEMESTER_NAMES[state.semester]}.`);
}

/* --------------------------------------------------------------------------
   Events
   -------------------------------------------------------------------------- */

function bindEvents() {
  els.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });
  els.semesterToggle.addEventListener("click", () => setSemester(state.semester === 1 ? 2 : 1));

  els.brandLink.addEventListener("click", (event) => {
    event.preventDefault();
    if (state.planSubjectIds.length) showLanding();
    else window.scrollTo({ top: 0, behavior: "smooth" });
  });

  els.degreeGrid.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-year]");
    if (!button || button.disabled) return;
    const yearId = button.dataset.year;
    const yearIds = yearSubjectIds(yearId) ?? [];
    // The year already being edited: go back to it with its groups.
    if (yearIds.length && sameSubjectSet(state.planSubjectIds, yearIds)) {
      resumePlan();
      return;
    }
    // Disabled only while loading, to ignore double clicks; it must be
    // enabled again afterwards or the year can never be opened again.
    button.disabled = true;
    try {
      const saved = readSaved();
      const samePlan = saved && sameSubjectSet(saved.subjectIds, yearIds);
      await openYear(yearId, {
        groupIds: samePlan ? saved.groupIds ?? [] : [],
        autoResult: samePlan ? saved.autoResult ?? null : null,
        customEvents: samePlan ? saved.customEvents ?? [] : undefined,
        pinnedGroupIds: samePlan ? saved.pinnedGroupIds ?? [] : undefined,
        preserveSelection: false,
      });
    } catch (error) {
      toast(error.message, { type: "error" });
    } finally {
      button.disabled = false;
    }
  });

  els.changeYear.addEventListener("click", showLanding);
  els.resumePlan.addEventListener("click", resumePlan);
  els.customPlan.addEventListener("click", openPlanDialog);
  els.editPlan.addEventListener("click", openPlanDialog);
  els.planSearch.addEventListener("input", renderPlanOptions);
  els.planClear.addEventListener("click", () => {
    state.planDraft.clear();
    for (const input of els.planOptions.querySelectorAll("[data-plan-subject]")) {
      input.checked = false;
    }
    updatePlanCount();
    refreshPlanYearButtons();
  });
  els.planOptions.addEventListener("change", (event) => {
    const input = event.target.closest("[data-plan-subject]");
    if (!input) return;
    if (input.checked) state.planDraft.add(input.value);
    else state.planDraft.delete(input.value);
    // A subject of the plan is listed twice: in "Tu matrícula" and in its degree.
    for (const twin of els.planOptions.querySelectorAll("[data-plan-subject]")) {
      if (twin.value === input.value) twin.checked = input.checked;
    }
    updatePlanCount();
    refreshPlanYearButtons();
  });
  els.planOptions.addEventListener("click", (event) => {
    const button = event.target.closest("[data-plan-year]");
    if (!button) return;
    const yearId = button.dataset.planYear;
    const ids = yearSubjectsById(yearId).map((subject) => subject.id);
    const allSelected = ids.length > 0 && ids.every((id) => state.planDraft.has(id));
    for (const id of ids) {
      if (allSelected) state.planDraft.delete(id);
      else state.planDraft.add(id);
    }
    const scrollTop = els.planOptions.scrollTop;
    renderPlanOptions();
    els.planOptions.scrollTop = scrollTop;
  });
  els.planApply.addEventListener("click", applyPlanDraft);

  // The "Resolver solapes" button is re-rendered with the alerts.
  els.alerts.addEventListener("click", (event) => {
    if (event.target.closest("[data-action='resolve']")) optimizeTimetable();
  });

  els.subjects.addEventListener("click", (event) => {
    const pill = event.target.closest("[data-group-id]");
    if (!pill) return;
    const { subjectId, type, groupId } = pill.dataset;
    // Several groups of the same type can be chosen at once (a lecturer may
    // teach CLE01 and CLE02), so a click only toggles that one group.
    const current = selectedOfType(subjectId, type);
    if (current.includes(groupId)) {
      state.selected[subjectId][type] = current.filter((id) => id !== groupId);
      if (!state.selected[subjectId][type].length) delete state.selected[subjectId][type];
      if (!Object.keys(state.selected[subjectId]).length) delete state.selected[subjectId];
    } else {
      setGroupSelection(groupId);
    }
    state.autoResult = null;
    onSelectionChanged();
  });

  els.timetable.addEventListener("click", (event) => {
    const eventBlock = event.target.closest("[data-event-id]");
    if (eventBlock) {
      openEventDialog(eventBlock.dataset.eventId);
      return;
    }
    const block = event.target.closest("[data-session-id]");
    if (!block) return;
    // A click fixes or frees the group for "Resolver solapes".
    togglePin(block.dataset.groupId);
  });

  els.addEventBtn.addEventListener("click", () => openEventDialog(null));
  els.weekSelect.addEventListener("change", () => {
    state.weekId = els.weekSelect.value || null;
    state.weekAuto = false;
    renderEverything();
    scheduleValidation();
  });
  els.eventList.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-event-id]");
    if (chip) openEventDialog(chip.dataset.eventId);
  });
  els.eventForm.addEventListener("submit", saveEventForm);
  els.eventDelete.addEventListener("click", deleteEditingEvent);

  els.exportBtn.addEventListener("click", () => {
    if (!selectedGroupIds().length) {
      toast("Elige algún grupo antes de exportar.", { type: "warn" });
      return;
    }
    els.exportDialog.showModal();
  });
  els.printBtn.addEventListener("click", () => window.print());
  document.getElementById("export-print").addEventListener("click", () => {
    els.exportDialog.close();
    setTimeout(() => window.print(), 120);
  });
  document.getElementById("export-json").addEventListener("click", downloadJson);
  document.getElementById("export-ics").addEventListener("click", downloadIcs);

  els.shareBtn.addEventListener("click", shareSchedule);
  els.copyShare.addEventListener("click", () => copyText(els.shareUrl.value));

  for (const button of document.querySelectorAll("[data-close-dialog]")) {
    button.addEventListener("click", () => button.closest("dialog")?.close());
  }
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }
}

/* --------------------------------------------------------------------------
   Landing
   -------------------------------------------------------------------------- */

function updateHeroCounts() {
  const counts = state.meta?.counts;
  const meta = els.landing?.querySelector(".hero-meta");
  if (!counts || !meta) return;
  meta.innerHTML = `
    <span><strong>${counts.degrees}</strong> grados</span>
    <span><strong>${counts.years}</strong> cursos</span>
    <span><strong>${counts.subjects}</strong> asignaturas</span>`;
}

function renderLanding() {
  els.degreeGrid.innerHTML = state.degrees
    .map((degree) => {
      const subjects = degree.years.reduce((sum, year) => sum + year.subject_count, 0);
      const years = degree.years
        .map(
          (year) => `
          <button class="year-btn" type="button" data-year="${escapeHtml(year.id)}">
            <span class="year-num">${year.number}º</span>
            <span class="year-label">${escapeHtml(year.name)}</span>
            <span class="year-count">${plural(year.subject_count, "asignatura", "asignaturas")}</span>
            <span class="year-arrow" aria-hidden="true">↗</span>
          </button>`,
        )
        .join("");
      return `
        <article class="degree-card">
          <h3 class="degree-name">${escapeHtml(degree.name)}</h3>
          <p class="degree-meta">${plural(degree.years.length, "curso", "cursos")} · ${plural(subjects, "asignatura", "asignaturas")}</p>
          <div class="year-grid">${years}</div>
        </article>`;
    })
    .join("");
}

function renderFatal(error) {
  els.landing.classList.remove("hidden");
  els.workspace.classList.add("hidden");
  els.yearChip.classList.add("hidden");
  els.shareBtn.classList.add("hidden");
  els.semesterToggle.classList.add("hidden");
  els.themeToggle.classList.remove("hidden");
  els.degreeGrid.innerHTML = `
    <article class="degree-card">
      <h3 class="degree-name">No se pudo cargar el catálogo</h3>
      <p class="degree-meta" style="margin-top:10px">${escapeHtml(error?.message ?? "Error desconocido")}</p>
      <p class="degree-meta" style="margin-top:6px">Comprueba que el backend esté arrancado y que <code>data/catalog.json</code> exista.</p>
    </article>`;
}

function showLanding() {
  renderResumeButton();
  els.landing.classList.remove("hidden");
  els.workspace.classList.add("hidden");
  els.yearChip.classList.add("hidden");
  els.shareBtn.classList.add("hidden");
  els.semesterToggle.classList.add("hidden");
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState({}, "", url);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* --------------------------------------------------------------------------
   Opening a plan (matrícula)
   -------------------------------------------------------------------------- */

async function openYear(
  yearId,
  {
    groupIds = [],
    autoResult = null,
    customEvents = undefined,
    pinnedGroupIds = undefined,
    preserveSelection = false,
  } = {},
) {
  const ids = yearSubjectIds(yearId);
  if (!ids?.length) {
    const data = await api.planForYear(yearId);
    return openPlan(data.subject_ids, {
      groupIds,
      autoResult,
      customEvents,
      pinnedGroupIds,
      preserveSelection,
    });
  }
  return openPlan(ids, {
    groupIds,
    autoResult,
    customEvents,
    pinnedGroupIds,
    preserveSelection,
  });
}

async function openPlan(
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
  state.validationKey = null;

  // Keep already-chosen groups whose subject is still in the new plan.
  for (const [subjectId, types] of Object.entries(previous)) {
    if (!state.planSubjectIds.includes(subjectId)) continue;
    for (const [type, groupIds] of Object.entries(types)) {
      for (const groupId of groupIds) {
        const group = state.groupIndex.get(groupId);
        if (group && group.subject_id === subjectId && group.type === type) {
          setGroupSelection(groupId);
        }
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
function showWorkspace() {
  els.yearChip.classList.remove("hidden");
  els.shareBtn.classList.remove("hidden");
  els.themeToggle.classList.remove("hidden");
  els.semesterToggle.classList.remove("hidden");
  els.landing.classList.add("hidden");
  els.workspace.classList.remove("hidden");
  updatePlanUrl();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// "Ir a mi horario" on the landing: the plan being edited, or else the
// one saved in this browser.
function renderResumeButton() {
  const saved = state.planSubjectIds.length ? null : readSaved();
  const label = state.planSubjectIds.length
    ? planChipLabel()
    : saved
      ? savedPlanLabel(saved.subjectIds)
      : "";
  els.resumePlan.classList.toggle("hidden", !label);
  els.resumePlanLabel.textContent = label;
}

// Same wording as the plan chip ("Matemáticas · 2º"), worked out from the
// degree directory because the saved plan is not loaded yet.
function savedPlanLabel(subjectIds) {
  const ids = new Set(subjectIds);
  const years = [];
  for (const degree of state.degrees) {
    for (const year of degree.years) {
      if ((year.subjects ?? []).some((subject) => ids.has(subject.id))) years.push({ degree, year });
    }
  }
  const degrees = [...new Set(years.map((item) => item.degree))];
  if (years.length === 1) return `${years[0].degree.short_name} · ${years[0].year.number}º`;
  if (degrees.length === 1) {
    return `${degrees[0].short_name} · ${years.map((item) => `${item.year.number}º`).join(" + ")}`;
  }
  return `${degrees.map((degree) => degree.short_name).join(" + ")} · ${plural(ids.size, "materia", "materias")}`;
}

async function resumePlan() {
  if (state.planSubjectIds.length) {
    showWorkspace();
    // The week shown follows today again, as when the app is opened.
    state.weekAuto = true;
    renderEverything();
    return;
  }
  const saved = readSaved();
  if (!saved) return;
  els.resumePlan.disabled = true;
  try {
    await openPlan(saved.subjectIds, {
      groupIds: saved.groupIds ?? [],
      autoResult: saved.autoResult ?? null,
      customEvents: saved.customEvents ?? [],
      pinnedGroupIds: saved.pinnedGroupIds ?? [],
    });
  } catch (error) {
    if (error?.status === 422) {
      // A catalogue refresh can invalidate subject ids saved in the browser.
      try {
        localStorage.removeItem(LS_LAST);
      } catch {
        /* ignore */
      }
      renderResumeButton();
      toast("La matrícula guardada ya no existe en el catálogo actual. Elige las materias de nuevo.", {
        type: "warn",
        duration: 6000,
      });
      return;
    }
    toast(`No se pudo abrir tu horario: ${error.message}`, { type: "error" });
  } finally {
    els.resumePlan.disabled = false;
  }
}

function updatePlanHeader() {
  const data = state.yearData;
  if (!data) return;
  const singleYear = data.years.length === 1 ? data.years[0] : null;
  const termCount = semesterSubjects().length;
  if (singleYear) {
    els.yearTitle.textContent = singleYear.name;
    els.yearSubtitle.textContent = `${singleYear.degree_name} · ${plural(termCount, "asignatura", "asignaturas")} en el ${SEMESTER_NAMES[state.semester]}`;
  } else {
    els.yearTitle.textContent = "Mi matrícula";
    els.yearSubtitle.textContent = `${plural(termCount, "materia", "materias")} en el ${SEMESTER_NAMES[state.semester]} · ${plural(data.years.length, "curso", "cursos")} · ${plural(data.degrees.length, "grado", "grados")}`;
  }
  els.yearChip.textContent = planChipLabel();
  els.yearChip.title = planLabel();
}

function distinctDegreeNames(useShortNames) {
  const names = [
    ...new Set(
      (state.yearData?.degrees ?? []).map((degree) =>
        useShortNames ? degree.short_name : degree.name,
      ),
    ),
  ];
  // "Matemáticas" is redundant when the plan also contains "Matemáticas + Física".
  return names.filter(
    (name, index) => !names.some((other, otherIndex) => otherIndex !== index && other.includes(name)),
  );
}

function planChipLabel() {
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

function planLabel() {
  const data = state.yearData;
  if (!data) return "Horario";
  if (data.years.length === 1) {
    const year = data.years[0];
    return `${year.degree_name} · ${year.name}`;
  }
  return `${distinctDegreeNames(false).join(" + ")} · ${plural(data.subjects.length, "materia", "materias")}`;
}

function updatePlanUrl() {
  if (!state.planSubjectIds.length) return;
  const url = new URL(window.location.href);
  const years = new Set(state.subjects.map((subject) => subject.year_id));
  const yearId = years.size === 1 ? [...years][0] : null;
  const fullYear = yearId ? yearSubjectIds(yearId) : null;
  const isFullYear =
    fullYear &&
    fullYear.length === state.planSubjectIds.length &&
    fullYear.every((subjectId) => state.planSubjectIds.includes(subjectId));
  if (isFullYear) {
    url.search = `?year=${encodeURIComponent(yearId)}`;
  } else {
    url.search = `?plan=${state.planSubjectIds.map(encodeURIComponent).join(",")}`;
  }
  window.history.replaceState({}, "", url);
}

/* --------------------------------------------------------------------------
   Plan / matrícula picker
   -------------------------------------------------------------------------- */

function normalizeSearch(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function yearSubjectsById(yearId) {
  for (const degree of state.degrees) {
    const year = degree.years.find((item) => item.id === yearId);
    if (year) return year.subjects ?? [];
  }
  return [];
}

function openPlanDialog() {
  state.planDraft = new Set(state.planSubjectIds);
  els.planSearch.value = "";
  renderPlanOptions();
  els.planDialog.showModal();
  window.setTimeout(() => els.planSearch.focus(), 60);
}

// Every double-degree subject is also offered, with the same groups and
// hours, by one of the single degrees.
function isDoubleDegree(degree) {
  return /^doble grado/i.test(degree.name);
}

function planSubjectRow(subject, year) {
  return `
    <label class="plan-subject" data-plan-year-scope="${escapeHtml(year.id)}">
      <input type="checkbox" value="${escapeHtml(subject.id)}" data-plan-subject
        data-plan-year-scope="${escapeHtml(year.id)}"
        ${state.planDraft.has(subject.id) ? "checked" : ""}>
      <span>
        <strong>${escapeHtml(subject.name)}</strong>
        <small>${subject.semester ?? 1}C · ${plural(subject.group_count ?? 0, "grupo", "grupos")} · ${plural(subject.session_count ?? 0, "sesión", "sesiones")}</small>
      </span>
    </label>`;
}

function planYearBlock(year, subjects, { title = `${year.number}º · ${year.name}`, toggle = true } = {}) {
  const allSelected = subjects.every((subject) => state.planDraft.has(subject.id));
  return `
    <div class="plan-year">
      <div class="plan-year-head">
        <strong>${escapeHtml(title)}</strong>
        <small>${plural(subjects.length, "materia", "materias")}</small>
        ${toggle ? `<button class="text-btn" type="button" data-plan-year="${escapeHtml(year.id)}">${allSelected ? "Quitar" : "Todo"}</button>` : ""}
      </div>
      <div class="plan-subject-grid">${subjects.map((subject) => planSubjectRow(subject, year)).join("")}</div>
    </div>`;
}

function renderPlanOptions() {
  const query = normalizeSearch(els.planSearch.value);
  const matches = (subject, degree, year) =>
    !query ||
    normalizeSearch(
      `${subject.name} ${degree.name} ${degree.short_name} ${year.name} ${year.number}`,
    ).includes(query);

  // "Tus asignaturas": the subjects of the plan as it was when the dialog was
  // opened, grouped by the degree and year they come from, so they can be
  // unticked without searching.  Unticking keeps them listed until applied.
  const planIds = new Set(state.planSubjectIds);
  const mineBlocks = [];
  for (const degree of state.degrees) {
    for (const year of degree.years) {
      const subjects = (year.subjects ?? []).filter(
        (subject) => planIds.has(subject.id) && matches(subject, degree, year),
      );
      if (!subjects.length) continue;
      mineBlocks.push(
        planYearBlock(year, subjects, {
          title: `${year.number}º · ${degree.name}`,
          toggle: false,
        }),
      );
    }
  }

  // The catalogue below leaves double degrees out so a search does not list
  // the same subject three times.
  const degreeBlocks = [];
  for (const degree of state.degrees) {
    if (isDoubleDegree(degree)) continue;
    const yearBlocks = [];
    for (const year of degree.years) {
      const subjects = (year.subjects ?? []).filter((subject) => matches(subject, degree, year));
      if (subjects.length) yearBlocks.push(planYearBlock(year, subjects));
    }
    if (yearBlocks.length) {
      degreeBlocks.push(`
        <section class="plan-degree">
          <h3 class="plan-degree-name">${escapeHtml(degree.name)}</h3>
          ${yearBlocks.join("")}
        </section>`);
    }
  }

  const mine = mineBlocks.length
    ? `
      <section class="plan-degree plan-mine">
        <h3 class="plan-degree-name">Tus asignaturas</h3>
        ${mineBlocks.join("")}
      </section>`
    : "";
  els.planOptions.innerHTML =
    mine || degreeBlocks.length
      ? mine + degreeBlocks.join("")
      : '<p class="plan-empty">No hay asignaturas que coincidan con la búsqueda.</p>';
  updatePlanCount();
  refreshPlanYearButtons();
}

function updatePlanCount() {
  els.planCount.textContent = plural(state.planDraft.size, "materia", "materias");
}

function refreshPlanYearButtons() {
  for (const button of els.planOptions.querySelectorAll("[data-plan-year]")) {
    const ids = yearSubjectsById(button.dataset.planYear).map((subject) => subject.id);
    const allSelected = ids.length > 0 && ids.every((id) => state.planDraft.has(id));
    button.textContent = allSelected ? "Quitar" : "Todo";
  }
}

async function applyPlanDraft() {
  const ids = [...state.planDraft];
  if (!ids.length) {
    toast("Selecciona al menos una materia.", { type: "warn" });
    return;
  }
  els.planDialog.close();
  try {
    await openPlan(ids, { preserveSelection: true });
    toast("Matrícula actualizada.");
  } catch (error) {
    toast(`No se pudo actualizar la matrícula: ${error.message}`, { type: "error" });
  }
}

function buildIndexes(data) {
  state.subjects = [];
  state.groupIndex = new Map();
  state.allSessions = [];
  state.sessionById = new Map();

  data.subjects.forEach((subject, subjectIndex) => {
    const color = PALETTE[subjectIndex % PALETTE.length];
    const groups = subject.groups.map((group) => {
      const enrichedSessions = group.sessions.map((session) => {
        const enriched = {
          ...session,
          color,
          subject_index: subjectIndex,
          subject_name: subject.name,
          group_code: group.code,
          group_type: group.type,
          group_type_label: group.type_label,
          has_conflict: false,
          conflict_with: [],
        };
        state.sessionById.set(session.id, enriched);
        state.allSessions.push(enriched);
        return enriched;
      });
      const enrichedGroup = {
        ...group,
        subject_id: subject.id,
        subject_name: subject.name,
        subject_index: subjectIndex,
        color,
        sessions: enrichedSessions,
      };
      state.groupIndex.set(group.id, enrichedGroup);
      return enrichedGroup;
    });
    state.subjects.push({ ...subject, color, groups });
  });
}

function setGroupSelection(groupId) {
  const group = state.groupIndex.get(groupId);
  if (!group) return;
  const bySubject = (state.selected[group.subject_id] = state.selected[group.subject_id] || {});
  const ids = (bySubject[group.type] = bySubject[group.type] || []);
  if (!ids.includes(group.id)) ids.push(group.id);
}

// Chosen groups of one subject and type, in the order they were picked.
function selectedOfType(subjectId, type) {
  return state.selected[subjectId]?.[type] ?? [];
}

function semesterSubjects() {
  return state.subjects.filter((subject) => (subject.semester ?? 1) === state.semester);
}

function semesterSubjectIds() {
  return semesterSubjects().map((subject) => subject.id);
}

// Groups of the term on screen; everything the timetable, validation and
// overlap repair look at.
function selectedGroupIds() {
  return groupIdsFor(semesterSubjects());
}

// Groups of both terms; what is saved and shared.
function allSelectedGroupIds() {
  return groupIdsFor(state.subjects);
}

function groupIdsFor(subjects) {
  const ids = [];
  for (const subject of subjects) {
    const selection = state.selected[subject.id];
    if (!selection) continue;
    for (const type of Object.keys(selection).sort((a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9))) {
      ids.push(...selection[type]);
    }
  }
  return ids;
}

function selectedCount() {
  return selectedGroupIds().length;
}

function selectedSubjectCount() {
  return semesterSubjects().filter(
    (subject) => Object.keys(state.selected[subject.id] || {}).length,
  ).length;
}

/* --------------------------------------------------------------------------
   Selection changes and validation
   -------------------------------------------------------------------------- */

function onSelectionChanged() {
  state.validation = null;
  state.validationKey = null;
  prunePins();
  renderEverything();
  saveLast();
  scheduleValidation();

  if (state.shareId) {
    state.shareId = null;
    updatePlanUrl();
  }
}

function scheduleValidation() {
  window.clearTimeout(state.validationTimer);
  state.validationTimer = window.setTimeout(refreshValidation, 170);
}

async function refreshValidation() {
  const ids = selectedGroupIds();
  const week = activeWeekId();
  const key = `${ids.join(",")}@${week ?? ""}`;
  const token = ++state.validationToken;
  if (!ids.length) {
    state.validation = null;
    state.validationKey = key;
    renderAlerts();
    renderStatus();
    return;
  }
  try {
    const result = await api.validate({
      group_ids: ids,
      subject_ids: semesterSubjectIds(),
      blocked: blockedSlots(),
      custom_events: state.customEvents,
      min_free_minutes: 30,
      week,
    });
    if (token !== state.validationToken) return;
    if (key !== `${selectedGroupIds().join(",")}@${activeWeekId() ?? ""}`) return;
    state.validation = result;
    state.validationKey = key;
    renderAlerts();
    renderStatus();
  } catch (error) {
    if (token !== state.validationToken) return;
    toast(`No se pudo validar el horario: ${error.message}`, { type: "error" });
  }
}

/* --------------------------------------------------------------------------
   Weeks
   -------------------------------------------------------------------------- */

const MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
const DAY_MS = 24 * 60 * 60 * 1000;

// ``weeks`` null means the meeting happens every week.
function sharesWeek(first, second) {
  return !first || !second || first.some((week) => second.includes(week));
}

function inWeek(session, weekId) {
  return !weekId || !session.weeks || session.weeks.includes(weekId);
}

// Monday (UTC midnight) of ISO week ``number`` of ``year``.
function isoMonday(year, number) {
  const jan4 = Date.UTC(year, 0, 4);
  const jan4Weekday = new Date(jan4).getUTCDay() || 7;
  return new Date(jan4 - (jan4Weekday - 1) * DAY_MS + (number - 1) * 7 * DAY_MS);
}

function isoWeeksInYear(year) {
  // 28 December always falls in the last ISO week of its year.
  const dec28 = Date.UTC(year, 11, 28);
  return Math.round((dec28 - isoMonday(year, 1).getTime()) / (7 * DAY_MS)) + 1;
}

function academicYearStart(date) {
  return date.getUTCMonth() >= 7 ? date.getUTCFullYear() : date.getUTCFullYear() - 1;
}

function todayUtc() {
  const now = new Date();
  return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
}

// Same rule as backend/timeutil.py: the catalogue may describe an earlier
// academic year, so each week keeps its ISO number in this year's course.
function weekInCurrentYear(weekId) {
  const calendar = state.term?.calendar ?? [];
  const first = calendar[0]?.id ?? weekId;
  const dataStart = academicYearStart(isoMonday(Number(first.slice(0, 4)), Number(first.slice(6))));
  const year = Number(weekId.slice(0, 4)) + academicYearStart(todayUtc()) - dataStart;
  return isoMonday(year, Math.min(Number(weekId.slice(6)), isoWeeksInYear(year)));
}

function formatWeekDate(weekId) {
  const monday = weekInCurrentYear(weekId);
  return `${monday.getUTCDate()} ${MONTHS[monday.getUTCMonth()]}`;
}

function currentWeekId() {
  const today = todayUtc();
  const monday = today.getTime() - ((today.getUTCDay() || 7) - 1) * DAY_MS;
  return (
    (state.term?.calendar ?? []).find((week) => weekInCurrentYear(week.id).getTime() === monday)
      ?.id ?? null
  );
}

// Weeks of the term on screen that differ for the chosen groups.  Identical
// weeks share one entry; the most frequent one is "Horario habitual".
function weekPatterns() {
  const weeks = (state.term?.calendar ?? []).filter((week) => week.semester === state.semester);
  if (!weeks.length) return [];
  const sessions = selectedGroupIds().flatMap((groupId) => state.groupIndex.get(groupId)?.sessions ?? []);
  const byKey = new Map();
  for (const week of weeks) {
    const ids = sessions.filter((session) => inWeek(session, week.id)).map((session) => session.id);
    // Weeks in which none of the chosen classes is taught are not offered.
    if (sessions.length && !ids.length) continue;
    const key = ids.sort().join(",");
    if (!byKey.has(key)) byKey.set(key, { weeks: [], sessionIds: new Set(ids) });
    byKey.get(key).weeks.push(week.id);
  }
  const patterns = [...byKey.values()];
  if (!patterns.length) return [];
  const usual = patterns.reduce((best, item) => (item.weeks.length > best.weeks.length ? item : best));
  usual.usual = true;
  const allWeeks = patterns.flatMap((item) => item.weeks).sort();
  const first = allWeeks[0];
  const last = allWeeks[allWeeks.length - 1];
  for (const pattern of patterns) {
    pattern.label = weekPatternLabel(pattern, usual, { first, last, alone: patterns.length === 1 });
  }
  return [usual, ...patterns.filter((item) => item !== usual)];
}

function weekPatternLabel(pattern, usual, { first, last, alone }) {
  if (pattern.usual) {
    return alone ? "Horario habitual" : `Horario habitual · ${plural(pattern.weeks.length, "semana", "semanas")}`;
  }
  const weeks = pattern.weeks;
  let title;
  if (weeks.length === 1 && weeks[0] === first) title = "Primera semana";
  else if (weeks.length === 1 && weeks[0] === last) title = "Última semana";
  else if (weeks.length === 1) title = `Semana del ${formatWeekDate(weeks[0])}`;
  else {
    const dates = weeks.slice(0, 2).map(formatWeekDate).join(", ");
    title = `${weeks.length} semanas: ${dates}${weeks.length > 2 ? "…" : ""}`;
  }
  // Classes are compared by group and hours: the same class in another room
  // is a room change, not one class more and one less.
  const slots = (ids) =>
    new Set(
      [...ids].map((id) => {
        const session = state.sessionById.get(id);
        return session ? `${session.group_id}|${session.day}|${session.start}|${session.end}` : id;
      }),
    );
  const mine = slots(pattern.sessionIds);
  const theirs = slots(usual.sessionIds);
  const added = [...mine].filter((slot) => !theirs.has(slot)).length;
  const removed = [...theirs].filter((slot) => !mine.has(slot)).length;
  const changes = [];
  if (added) changes.push(`${plural(added, "clase", "clases")} más`);
  if (removed) changes.push(`${plural(removed, "clase", "clases")} menos`);
  if (!changes.length) changes.push("cambio de aula");
  return `${title} · ${changes.join(", ")}`;
}

// The week whose classes are on screen, or null when the catalogue has a
// single typical week.  Keeps ``state.weekId`` pointing at a valid week.
function activeWeekId() {
  const patterns = weekPatterns();
  if (!patterns.length) return null;
  const offered = patterns.flatMap((pattern) => pattern.weeks);
  if (state.weekAuto) {
    const today = currentWeekId();
    state.weekId = offered.includes(today) ? today : patterns[0].weeks[0];
  } else if (!offered.includes(state.weekId)) {
    state.weekId = patterns[0].weeks[0];
  }
  return state.weekId;
}

function renderWeekSelect() {
  const weekId = activeWeekId();
  const patterns = weekPatterns();
  els.weekPicker.classList.toggle("hidden", !weekId);
  if (!weekId) return;
  const today = currentWeekId();
  els.weekSelect.innerHTML = patterns
    .map((pattern) => {
      const selected = pattern.weeks.includes(weekId);
      // The entry of the current week opens on today's week, not its first.
      const value = selected ? weekId : pattern.weeks.includes(today) ? today : pattern.weeks[0];
      const label = pattern.weeks.includes(today) ? `${pattern.label} (esta semana)` : pattern.label;
      return `<option value="${escapeHtml(value)}" ${selected ? "selected" : ""}>${escapeHtml(label)}</option>`;
    })
    .join("");
  const current = patterns.find((pattern) => pattern.weeks.includes(weekId));
  els.weekPicker.title = current && !current.usual
    ? `Semanas: ${current.weeks.map(formatWeekDate).join(", ")}`
    : "Semana que se muestra";
}

/* --------------------------------------------------------------------------
   Schedule computation (client-side, instant)
   -------------------------------------------------------------------------- */

function mergeLocalSessions(sessions) {
  const groups = new Map();
  for (const session of sessions) {
    const key = [
      session.subject_id,
      session.group_id,
      session.type,
      session.day,
      session.room_id,
      session.building_id,
    ].join("|");
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(session);
  }

  const merged = [];
  for (const groupSessions of groups.values()) {
    groupSessions.sort((a, b) => toMinutes(a.start) - toMinutes(b.start));
    let current = null;
    for (const session of groupSessions) {
      if (!current) {
        current = {
          ...session,
          id: `blk-${session.id}`,
          source_session_ids: [session.id],
          has_conflict: false,
          has_event_conflict: false,
          conflict_with: [],
        };
        continue;
      }
      if (current.end === session.start) {
        current.end = session.end;
        current.duration_minutes += session.duration_minutes;
        current.source_session_ids.push(session.id);
        current.pinned = current.pinned || session.pinned;
        current.has_pref_conflict = current.has_pref_conflict || session.has_pref_conflict;
      } else {
        merged.push(current);
        current = {
          ...session,
          id: `blk-${session.id}`,
          source_session_ids: [session.id],
          has_conflict: false,
          has_event_conflict: false,
          conflict_with: [],
        };
      }
    }
    if (current) merged.push(current);
  }
  return merged.sort(
    (a, b) =>
      (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) ||
      toMinutes(a.start) - toMinutes(b.start) ||
      a.subject_name.localeCompare(b.subject_name, "es"),
  );
}

function buildLocalSchedule() {
  const rawSessions = [];
  const blocked = blockedSlots();
  const weekId = activeWeekId();
  for (const groupId of selectedGroupIds()) {
    const group = state.groupIndex.get(groupId);
    if (!group) continue;
    for (const session of group.sessions) {
      if (!inWeek(session, weekId)) continue;
      const prefersConflict = blocked.some(
        (slot) =>
          session.day === slot.day &&
          toMinutes(session.start) < toMinutes(slot.end) &&
          toMinutes(slot.start) < toMinutes(session.end),
      );
      rawSessions.push({
        ...session,
        pinned: isPinnedGroup(groupId),
        has_conflict: false,
        has_pref_conflict: prefersConflict,
        conflict_with: [],
      });
    }
  }
  const sessions = mergeLocalSessions(rawSessions);

  const conflicts = [];
  for (let i = 0; i < sessions.length; i += 1) {
    for (let j = i + 1; j < sessions.length; j += 1) {
      const a = sessions[i];
      const b = sessions[j];
      if (a.day !== b.day) continue;
      const start = Math.max(toMinutes(a.start), toMinutes(b.start));
      const end = Math.min(toMinutes(a.end), toMinutes(b.end));
      if (start >= end) continue;
      a.has_conflict = true;
      b.has_conflict = true;
      a.conflict_with.push(b.id);
      b.conflict_with.push(a.id);
      conflicts.push({
        day: a.day,
        day_label: DAY_LABELS[a.day],
        start: minutesToHm(start),
        end: minutesToHm(end),
        overlap_minutes: end - start,
        left: a,
        right: b,
      });
    }
  }
  conflicts.sort(
    (a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start),
  );

  const eventConflicts = [];
  for (const event of state.customEvents) {
    for (const session of sessions) {
      if (session.day !== event.day) continue;
      const start = Math.max(toMinutes(session.start), toMinutes(event.start));
      const end = Math.min(toMinutes(session.end), toMinutes(event.end));
      if (start >= end) continue;
      session.has_event_conflict = true;
      eventConflicts.push({
        event,
        session,
        day: event.day,
        day_label: DAY_LABELS[event.day],
        start: minutesToHm(start),
        end: minutesToHm(end),
        overlap_minutes: end - start,
      });
    }
  }
  eventConflicts.sort(
    (a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start),
  );

  return { sessions, conflicts, eventConflicts };
}

function minutesToHm(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function sessionsOverlap(a, b) {
  if (a.day !== b.day || !sharesWeek(a.weeks, b.weeks)) return false;
  return toMinutes(a.start) < toMinutes(b.end) && toMinutes(b.start) < toMinutes(a.end);
}

function groupConflicts(group) {
  const candidateSessions = group.sessions;
  for (const selectedId of selectedGroupIds()) {
    const selectedGroup = state.groupIndex.get(selectedId);
    // Picking a group no longer replaces its siblings, so only the group
    // itself is skipped: CLE01 and CLE02 at the same hour do clash.
    if (!selectedGroup || selectedGroup.id === group.id) continue;
    for (const candidate of candidateSessions) {
      for (const selected of selectedGroup.sessions) {
        if (sessionsOverlap(candidate, selected)) return true;
      }
    }
  }
  return false;
}

function groupHitsPreferences(group) {
  const blocked = blockedSlots();
  for (const session of group.sessions) {
    for (const slot of blocked) {
      if (
        session.day === slot.day &&
        toMinutes(session.start) < toMinutes(slot.end) &&
        toMinutes(slot.start) < toMinutes(session.end)
      ) {
        return true;
      }
    }
  }
  return false;
}

function localBlockedConflicts(sessions) {
  const blocked = blockedSlots();
  const result = [];
  for (const session of sessions) {
    for (const slot of blocked) {
      if (
        session.day === slot.day &&
        toMinutes(session.start) < toMinutes(slot.end) &&
        toMinutes(slot.start) < toMinutes(session.end)
      ) {
        result.push({ session, slot });
        break;
      }
    }
  }
  return result;
}

/* --------------------------------------------------------------------------
   Rendering — sidebar
   -------------------------------------------------------------------------- */

function renderEverything() {
  renderWeekSelect();
  renderSubjects();
  renderSchedule();
  renderStatus();
  renderAlerts();
  renderEventList();
}

function renderSubjects() {
  if (!state.subjects.length) return;
  const scrollTop = els.subjects.scrollTop;
  const showOrigin =
    (state.yearData?.years?.length ?? 0) > 1 || (state.yearData?.degrees?.length ?? 0) > 1;
  const subjects = semesterSubjects();
  if (!subjects.length) {
    els.subjects.innerHTML = `<p class="empty-copy">Tu matrícula no tiene asignaturas del ${SEMESTER_NAMES[state.semester]}. Cambia de cuatrimestre arriba o edita la matrícula.</p>`;
    return;
  }
  els.subjects.innerHTML = subjects
    .map((subject) => {
      const selection = state.selected[subject.id] || {};
      const types = [...new Set(subject.groups.map((group) => group.type))].sort(
        (a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9),
      );
      const selectedTypes = types.filter((type) => selection[type]?.length);
      const rows = types
        .map((type) => {
          const groups = subject.groups
            .filter((group) => group.type === type)
            .sort((a, b) => a.code.localeCompare(b.code, "es", { numeric: true }));
          const typeLabel = groups[0]?.type_label ?? type;
          const missing = !selection[type]?.length;
          const pills = groups
            .map((group) => {
              const selected = Boolean(selection[type]?.includes(group.id));
              const pinned = isPinnedGroup(group.id);
              const conflict = groupConflicts(group);
              const pref = groupHitsPreferences(group);
              const classes = [
                "group-pill",
                selected ? "is-selected" : "",
                pinned ? "is-pinned" : "",
                conflict ? "is-conflict" : "",
                pref ? "is-pref-conflict" : "",
              ]
                .filter(Boolean)
                .join(" ");
              const tooltip = [
                `${group.code} · ${group.type_label}`,
                ...group.sessions.map(
                  (session) =>
                    `${DAY_LABELS[session.day]} ${session.start}–${session.end} · ${session.room_name} (${session.building_name})`,
                ),
                conflict ? "Solapa con la selección actual" : "",
                pref ? "Cae en una franja que querías evitar" : "",
                pinned ? "Grupo fijado: el generador respetará sus horas" : "",
              ]
                .filter(Boolean)
                .join("\n");
              return `
                <button
                  class="${classes}"
                  type="button"
                  data-subject-id="${escapeHtml(subject.id)}"
                  data-type="${escapeHtml(type)}"
                  data-group-id="${escapeHtml(group.id)}"
                  title="${escapeHtml(tooltip)}"
                  aria-pressed="${selected}"
                >
                  ${escapeHtml(group.code)}
                  <span class="pill-pin" title="Fijado" aria-hidden="true">⚑</span>
                  <span class="pill-close" aria-hidden="true">×</span>
                </button>`;
            })
            .join("");
          return `
            <div class="group-type">
              <div class="group-type-head">
                <span>${escapeHtml(typeLabel)}</span>
                ${missing ? '<span class="missing">sin elegir</span>' : ""}
              </div>
              <div class="group-options">${pills}</div>
            </div>`;
        })
        .join("");
      const weeklyMinutes = selectedTypes
        .flatMap((type) => selection[type].map((groupId) => state.groupIndex.get(groupId)))
        .filter(Boolean)
        .reduce((sum, group) => sum + group.weekly_minutes, 0);
      return `
        <article class="subject-card ${selectedTypes.length ? "has-selection" : ""}" style="--subject-color:${subject.color}">
          <header class="subject-head">
            <span class="subject-dot" aria-hidden="true"></span>
            <div class="subject-title">
              <span class="subject-name">${escapeHtml(subject.name)}</span>
              ${showOrigin ? `<span class="subject-origin">${escapeHtml(subject.origin_label ?? "")}</span>` : ""}
              <span class="subject-hours">${selectedTypes.length ? `${formatHours(weeklyMinutes)} / semana` : `${subject.groups.length} grupos disponibles`}</span>
            </div>
            <span class="subject-check ${selectedTypes.length === types.length ? "is-complete" : ""}">
              ${selectedTypes.length}/${types.length}
            </span>
          </header>
          ${rows}
        </article>`;
    })
    .join("");
  els.subjects.scrollTop = scrollTop;
}

/* --------------------------------------------------------------------------
   Rendering — timetable and status
   -------------------------------------------------------------------------- */

function renderSchedule() {
  if (!state.term) return;
  const schedule = buildLocalSchedule();
  state.localSchedule = schedule;
  const conflictEventIds = new Set(
    schedule.eventConflicts.map((conflict) => conflict.event.id),
  );
  const events = state.customEvents.map((event) => ({
    ...event,
    color: safeEventColor(event.color),
    has_event_conflict: conflictEventIds.has(event.id),
  }));
  renderTimetable(els.timetable, {
    sessions: schedule.sessions,
    events,
    term: state.term,
  });
}

function renderStatus() {
  const { sessions, conflicts, eventConflicts } = state.localSchedule;
  const blocked = localBlockedConflicts(sessions);
  const missing = missingGroups();
  const groupCount = selectedCount();
  let label = "Sin grupos";
  let className = "status-pill is-empty";

  if (groupCount) {
    if (conflicts.length) {
      label = plural(conflicts.length, "solape", "solapes");
      className = "status-pill is-danger";
    } else if (eventConflicts.length) {
      label = `${plural(eventConflicts.length, "cruce", "cruces")} con extraescolares`;
      className = "status-pill is-warn";
    } else if (missing.length) {
      label = `${plural(missing.length, "grupo pendiente", "grupos pendientes")}`;
      className = "status-pill is-warn";
    } else if (blocked.length) {
      label = `${plural(blocked.length, "clase fuera de preferencias", "clases fuera de preferencias")}`;
      className = "status-pill is-warn";
    } else if (state.autoResult?.status === "partial") {
      const total = state.autoResult.requested_subject_ids?.length ?? 0;
      const included = total - (state.autoResult.excluded_subjects?.length ?? 0);
      label = `${included} de ${total} materias`;
      className = "status-pill";
    } else if (otherWeekConflicts().length) {
      label = "Sin solapes esta semana";
      className = "status-pill is-warn";
    } else {
      label = "Sin solapes";
      className = "status-pill";
    }
  }
  els.conflictPill.textContent = label;
  els.conflictPill.className = className;

  const minutes = sessions.reduce((sum, session) => sum + session.duration_minutes, 0);
  const days = new Set(sessions.map((session) => session.day)).size;
  els.hoursStat.textContent = `${formatHours(minutes)} / semana`;
  els.daysStat.textContent = plural(days, "día", "días");
  els.selectionCount.textContent = `${plural(groupCount, "grupo", "grupos")} · ${plural(selectedSubjectCount(), "materia", "materias")}`;
  els.shareBtn.disabled = groupCount === 0;
  els.exportBtn.disabled = groupCount === 0;
}

function missingGroups() {
  const missing = [];
  const deliberatelyExcluded = new Set(
    state.autoResult?.status === "partial"
      ? (state.autoResult.excluded_subjects || []).map((subject) => subject.id)
      : [],
  );
  for (const subject of semesterSubjects()) {
    if (deliberatelyExcluded.has(subject.id)) continue;
    const selection = state.selected[subject.id] || {};
    const types = [...new Set(subject.groups.map((group) => group.type))];
    for (const type of types) {
      if (!selection[type]?.length) {
        const group = subject.groups.find((item) => item.type === type);
        missing.push({ subject, type, typeLabel: group?.type_label ?? type });
      }
    }
  }
  return missing;
}

// Term-wide overlaps (from the API) that do not happen in the week on
// screen, so a clash in a single special week is still visible.
function otherWeekConflicts() {
  const weekId = activeWeekId();
  if (!weekId || !state.validation?.conflicts) return [];
  const seen = new Set();
  const result = [];
  for (const conflict of state.validation.conflicts) {
    const left = state.sessionById.get(conflict.left.id);
    const right = state.sessionById.get(conflict.right.id);
    if (!left || !right) continue;
    const weeks = left.weeks && right.weeks
      ? left.weeks.filter((week) => right.weeks.includes(week))
      : left.weeks ?? right.weeks;
    if (!weeks || weeks.includes(weekId)) continue;
    const key = [conflict.left.subject_name, conflict.left.group_code, conflict.right.subject_name, conflict.right.group_code, conflict.day, weeks.join()].join("|");
    if (seen.has(key)) continue;
    seen.add(key);
    const when = weeks.length === 1
      ? `semana del ${formatWeekDate(weeks[0])}`
      : `${weeks.length} semanas desde el ${formatWeekDate(weeks[0])}`;
    result.push({ ...conflict, when });
  }
  return result;
}

function renderAlerts() {
  const alerts = [];
  const { conflicts, sessions, eventConflicts } = state.localSchedule;
  const blocked = localBlockedConflicts(sessions);
  const missing = missingGroups();

  if (conflicts.length) {
    const items = conflicts.slice(0, 4).map(
      (conflict) =>
        `<li>${escapeHtml(conflict.left.subject_name)} (${escapeHtml(conflict.left.group_code)}) × ${escapeHtml(conflict.right.subject_name)} (${escapeHtml(conflict.right.group_code)}) · ${escapeHtml(conflict.day_label)} ${conflict.start}–${conflict.end}</li>`,
    );
    alerts.push(`
      <div class="alert alert-danger">
        <span class="alert-icon">!</span>
        <div><strong>Hay ${plural(conflicts.length, "solape", "solapes")} en tu semana.</strong>
        <ul>${items.join("")}</ul>${resolveButton()}</div>
      </div>`);
  }

  const elsewhere = otherWeekConflicts();
  if (elsewhere.length) {
    const items = elsewhere.slice(0, 4).map(
      (item) =>
        `<li>${escapeHtml(item.left.subject_name)} (${escapeHtml(item.left.group_code)}) × ${escapeHtml(item.right.subject_name)} (${escapeHtml(item.right.group_code)}) · ${escapeHtml(item.day_label)} ${item.start}–${item.end} · ${escapeHtml(item.when)}</li>`,
    );
    alerts.push(`
      <div class="alert alert-warn">
        <span class="alert-icon">!</span>
        <div><strong>${plural(elsewhere.length, "solape", "solapes")} en otras semanas.</strong>
        <ul>${items.join("")}${elsewhere.length > items.length ? `<li>y ${elsewhere.length - items.length} más</li>` : ""}</ul>${conflicts.length ? "" : resolveButton()}</div>
      </div>`);
  }

  if (eventConflicts.length) {
    const items = eventConflicts.slice(0, 4).map(
      (conflict) =>
        `<li>${escapeHtml(conflict.event.title)} × ${escapeHtml(conflict.session.subject_name)} · ${escapeHtml(conflict.day_label)} ${conflict.start}–${conflict.end}</li>`,
    );
    alerts.push(`
      <div class="alert alert-warn">
        <span class="alert-icon">⚑</span>
        <div><strong>Hay ${plural(eventConflicts.length, "cruce", "cruces")} con actividades extraescolares.</strong>
        <ul>${items.join("")}</ul></div>
      </div>`);
  }

  if (missing.length) {
    const visible = missing.slice(0, 5);
    const items = visible.map(
      (item) => `<li>${escapeHtml(item.subject.name)} · ${escapeHtml(item.typeLabel)}</li>`,
    );
    alerts.push(`
      <div class="alert alert-warn">
        <span class="alert-icon">○</span>
        <div><strong>Te falta elegir ${plural(missing.length, "grupo", "grupos")}.</strong>
        <ul>${items.join("")}${missing.length > visible.length ? `<li>y ${missing.length - visible.length} más</li>` : ""}</ul></div>
      </div>`);
  }

  if (blocked.length) {
    const items = blocked.slice(0, 4).map(
      (item) =>
        `<li>${escapeHtml(item.session.subject_name)} · ${escapeHtml(item.session.day_label)} ${item.session.start}–${item.session.end}</li>`,
    );
    alerts.push(`
      <div class="alert alert-info">
        <span class="alert-icon">◷</span>
        <div><strong>${plural(blocked.length, "clase cae", "clases caen")} en franjas que querías evitar.</strong>
        <ul>${items.join("")}</ul></div>
      </div>`);
  }

  if (state.autoResult?.status === "partial" && state.autoResult.excluded_subjects?.length) {
    const total = state.autoResult.requested_subject_ids?.length ?? 0;
    const included = total - state.autoResult.excluded_subjects.length;
    const names = state.autoResult.excluded_subjects.map((subject) => `<li>${escapeHtml(subject.name)}</li>`).join("");
    alerts.push(`
      <div class="alert alert-info">
        <span class="alert-icon">✦</span>
        <div><strong>El horario automático encaja ${included} de ${total} materias.</strong>
        No caben sin solapes: <ul>${names}</ul>Añádelas a mano si quieres asumir el solape.</div>
      </div>`);
  }

  if (state.autoResult?.status === "relaxed" && conflicts.length) {
    alerts.push(`
      <div class="alert alert-warn">
        <span class="alert-icon">≈</span>
        <div><strong>No existe una combinación sin solapes con esas preferencias.</strong>
        Te mostramos la mejor aproximación; prueba a quitar «Sin viernes» o «Sin 8:00».</div>
      </div>`);
  }

  els.alerts.innerHTML = alerts.join("");
}

/* --------------------------------------------------------------------------
   Pinned groups
   -------------------------------------------------------------------------- */

// Clicking a class fixes its group: "Resolver solapes" will not change it.
function togglePin(groupId) {
  if (!groupId) return;
  const group = state.groupIndex.get(groupId);
  const name = group ? `${group.code}` : "Grupo";
  if (state.pinnedGroupIds.has(groupId)) {
    state.pinnedGroupIds.delete(groupId);
    toast(`${name} desfijado.`);
  } else {
    state.pinnedGroupIds.add(groupId);
    toast(`${name} fijado: «Resolver solapes» no lo cambiará.`);
  }
  prunePins();
  renderSubjects();
  renderSchedule();
  renderStatus();
  saveLast();
}

/* --------------------------------------------------------------------------
   Overlap repair
   -------------------------------------------------------------------------- */

function resolveButton() {
  return `
    <button class="btn alert-action" type="button" data-action="resolve" ${state.resolving ? "disabled" : ""}>
      ${state.resolving ? '<span class="spinner" aria-hidden="true"></span> Buscando el arreglo más pequeño…' : "✦ Resolver solapes"}
    </button>
    <p class="alert-note">Cambia los menos grupos posibles entre los que ya has elegido. Los grupos fijados (⚑, clic en la clase) no se tocan.</p>`;
}

function describeChanges(changes) {
  const moved = changes.filter((change) => change.from_group_id && change.to_group_id);
  if (!moved.length) return "";
  const sample = moved
    .slice(0, 3)
    .map((change) => `${change.subject_name} (${change.from_group_code} → ${change.to_group_code})`)
    .join(", ");
  return moved.length > 3 ? `${sample} y ${moved.length - 3} más` : sample;
}

async function optimizeTimetable() {
  if (!state.planSubjectIds.length) return;
  // Only the groups already chosen are repaired; nothing new is added.
  if (!selectedGroupIds().length) {
    toast("Elige algún grupo antes de resolver solapes.", { type: "warn" });
    return;
  }
  if (state.resolving) return;
  state.resolving = true;
  renderAlerts();
  try {
    const termIds = new Set(selectedGroupIds());
    const result = await api.optimize({
      subject_ids: semesterSubjectIds(),
      group_ids: [...termIds],
      blocked: blockedSlots(),
      custom_events: state.customEvents,
      pinned_group_ids: [...state.pinnedGroupIds].filter((groupId) => termIds.has(groupId)),
    });
    if (result.status === "unchanged") {
      toast("Tu horario no tiene solapes: no hay nada que cambiar.");
      return;
    }
    state.autoResult = null;
    // Only the visible term is replaced; the other keeps its groups.
    for (const subjectId of semesterSubjectIds()) delete state.selected[subjectId];
    for (const groupId of result.group_ids || []) setGroupSelection(groupId);
    prunePins();
    state.shareId = null;
    // The repair covers the whole term; free slots are asked for the week on screen.
    state.validation = null;
    state.validationKey = null;

    updatePlanUrl();

    renderEverything();
    saveLast();
    scheduleValidation();

    const changes = result.changes || [];
    const changed = result.solver?.changed ?? 0;
    const removed = result.solver?.removed ?? 0;
    const detail = describeChanges(changes);
    if (result.status === "optimized") {
      const summary = `Solapes resueltos cambiando ${plural(changed, "grupo", "grupos")}`;
      toast(detail ? `${summary}: ${detail}.` : `${summary}.`, { duration: 7000 });
    } else if (result.status === "partial") {
      const dropped = changes
        .filter((change) => change.from_group_id && !change.to_group_id)
        .map((change) => `${change.subject_name} (${change.type_label})`)
        .join(", ");
      toast(
        `No caben todos los grupos sin solapes: se han quitado ${plural(removed, "grupo", "grupos")} (${dropped})` +
          (changed ? ` y cambiado ${plural(changed, "grupo", "grupos")}.` : "."),
        { type: "warn", duration: 8000 },
      );
    } else {
      toast("No se pudo quitar los solapes sin mover los grupos fijados.", { type: "error" });
    }
  } catch (error) {
    toast(`No se pudo resolver los solapes: ${error.message}`, { type: "error" });
  } finally {
    state.resolving = false;
    renderAlerts();
  }
}

/* --------------------------------------------------------------------------
   Share, export, toast
   -------------------------------------------------------------------------- */

async function loadShared(shareId) {
  try {
    const response = await api.shared(shareId);
    const share = response.share;
    const subjectIds =
      share.subject_ids?.length
        ? share.subject_ids
        : share.year_id
          ? yearSubjectIds(share.year_id) ?? []
          : [...new Set((share.selections || []).map((selection) => selection.subject_id))];
    if (!subjectIds.length) throw new Error("El enlace no contiene una matrícula válida");
    await openPlan(subjectIds, {
      groupIds: share.group_ids,
      customEvents: share.custom_events ?? [],
      pinnedGroupIds: share.pinned_group_ids ?? [],
    });
    state.shareId = share.id;
    renderEverything();
    const url = new URL(window.location.href);
    url.search = `?s=${encodeURIComponent(share.id)}`;
    window.history.replaceState({}, "", url);
    toast("Horario compartido cargado.");
  } catch (error) {
    toast(`No se pudo abrir el horario compartido: ${error.message}`, { type: "error", duration: 6000 });
    showLanding();
  }
}

async function shareSchedule() {
  const ids = allSelectedGroupIds();
  if (!ids.length) {
    toast("Elige algún grupo antes de compartir.", { type: "warn" });
    return;
  }
  els.shareBtn.disabled = true;
  try {
    const response = await api.share({
      subject_ids: state.planSubjectIds,
      title: planLabel(),
      group_ids: ids,
      custom_events: state.customEvents,
      pinned_group_ids: [...state.pinnedGroupIds],
    });
    const url = new URL(window.location.href);
    url.search = `?s=${encodeURIComponent(response.share.id)}`;
    els.shareUrl.value = url.toString();
    els.shareNote.textContent = `Copia guardada con id ${response.share.id}. Cualquiera con el enlace verá esta combinación.`;
    els.shareDialog.showModal();
    setTimeout(() => els.shareUrl.select(), 80);
  } catch (error) {
    toast(`No se pudo compartir: ${error.message}`, { type: "error" });
  } finally {
    els.shareBtn.disabled = false;
  }
}

function downloadJson() {
  const data = state.yearData;
  const payload = {
    schema_version: "2.0",
    exported_at: new Date().toISOString(),
    term: data?.term,
    plan: {
      label: planLabel(),
      subject_ids: state.planSubjectIds,
      years: data?.years,
      degrees: data?.degrees,
    },
    degree: data?.degrees?.length === 1 ? data.degrees[0] : null,
    year: data?.years?.length === 1 ? data.years[0] : null,
    group_ids: allSelectedGroupIds(),
    pinned_group_ids: [...state.pinnedGroupIds],
    custom_events: state.customEvents,
    selections: state.subjects
      .map((subject) => {
        const selection = state.selected[subject.id] || {};
        return {
          subject_id: subject.id,
          subject_name: subject.name,
          semester: subject.semester ?? 1,
          groups: Object.entries(selection).flatMap(([type, groupIds]) => groupIds.map((groupId) => {
            const group = state.groupIndex.get(groupId);
            return {
              group_id: groupId,
              code: group?.code,
              type,
              type_label: group?.type_label,
              sessions: group?.sessions.map((session) => ({
                id: session.id,
                day: session.day,
                start: session.start,
                end: session.end,
                room_id: session.room_id,
                room_name: session.room_name,
                building_id: session.building_id,
                building_name: session.building_name,
              })),
            };
          })),
        };
      })
      .filter((subject) => subject.groups.length),
    stats: state.validation?.stats ?? null,
  };
  downloadBlob(
    JSON.stringify(payload, null, 2),
    `horario-${slugify(planChipLabel() || "usc")}.json`,
    "application/json",
  );
  toast("JSON descargado.");
}

async function downloadIcs() {
  const ids = selectedGroupIds();
  if (!ids.length) return;
  try {
    const response = await fetch("/api/schedules/export.ics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        group_ids: ids,
        name: `Horario ${planChipLabel() || "USC"}`,
        custom_events: state.customEvents,
      }),
    });
    if (!response.ok) {
      let detail = `Error ${response.status}`;
      try {
        const payload = await response.json();
        if (typeof payload.detail === "string") detail = payload.detail;
      } catch {
        /* keep status */
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    downloadBlob(blob, `horario-${slugify(planChipLabel() || "usc")}.ics`, "text/calendar");
    toast("Calendario .ics descargado.");
  } catch (error) {
    toast(`No se pudo exportar el calendario: ${error.message}`, { type: "error" });
  }
}

function slugify(value) {
  return String(value)
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function downloadBlob(content, filename, type) {
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

async function copyText(text) {
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

function toast(message, { type = "info", duration = 4200 } = {}) {
  if (!els.toast) return;
  const node = document.createElement("div");
  node.className = `toast${type === "error" ? " is-error" : type === "warn" ? " is-warn" : ""}`;
  node.textContent = message;
  els.toast.appendChild(node);
  while (els.toast.children.length > 3) els.toast.firstElementChild.remove();
  window.setTimeout(() => node.remove(), duration);
}
