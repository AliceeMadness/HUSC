import { api } from "./api.js";
import { DAYS, formatDuration, renderTimetable, toMinutes } from "./timetable.js";

/* --------------------------------------------------------------------------
   Constants and state
   -------------------------------------------------------------------------- */

const PALETTE = [
  "#2f7d6b",
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
const LS_PREFS = "horario:prefs-v2";
const LS_THEME = "horario:theme";

const state = {
  meta: null,
  degrees: [],
  term: null,
  yearData: null,
  yearId: null,
  shareId: null,
  subjects: [],
  groupIndex: new Map(),
  allSessions: [],
  sessionById: new Map(),
  selected: {},
  prefs: readPrefs(),
  minFree: 60,
  localSchedule: { sessions: [], conflicts: [] },
  validation: null,
  validationKey: null,
  autoResult: null,
  moveSession: null,
  moveCandidates: [],
  moveLoading: false,
  moveToken: 0,
  previewCandidate: null,
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
    shareBtn: document.getElementById("share-btn"),
    brandLink: document.getElementById("brand-link"),
    changeYear: document.getElementById("change-year"),
    yearTitle: document.getElementById("year-title"),
    yearSubtitle: document.getElementById("year-subtitle"),
    autoBtn: document.getElementById("auto-btn"),
    autoHint: document.getElementById("auto-hint"),
    prefFriday: document.getElementById("pref-no-friday"),
    prefEarly: document.getElementById("pref-no-early"),
    prefLate: document.getElementById("pref-no-late"),
    subjects: document.getElementById("subjects"),
    selectionCount: document.getElementById("selection-count"),
    conflictPill: document.getElementById("conflict-pill"),
    hoursStat: document.getElementById("hours-stat"),
    daysStat: document.getElementById("days-stat"),
    alerts: document.getElementById("alerts"),
    timetable: document.getElementById("timetable"),
    freeSlots: document.getElementById("free-slots"),
    freeDuration: document.getElementById("free-duration"),
    moveCard: document.getElementById("move-card"),
    movePanel: document.getElementById("move-panel"),
    exitMove: document.getElementById("exit-move"),
    exportBtn: document.getElementById("export-btn"),
    printBtn: document.getElementById("print-btn"),
    shareDialog: document.getElementById("share-dialog"),
    shareUrl: document.getElementById("share-url"),
    shareNote: document.getElementById("share-note"),
    copyShare: document.getElementById("copy-share"),
    exportDialog: document.getElementById("export-dialog"),
    toast: document.getElementById("toast"),
  };

  const preferredTheme =
    localStorage.getItem(LS_THEME) ||
    (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  applyTheme(preferredTheme, false);
  bindEvents();
  renderPrefs();
  updateFreeDurationButtons();

  try {
    const [meta, directory] = await Promise.all([api.health(), api.degrees()]);
    state.meta = meta;
    state.term = directory.term;
    state.degrees = directory.degrees;
    renderLanding();
    updateHeroCounts();

    const params = new URLSearchParams(window.location.search);
    const shareId = params.get("s");
    if (shareId) {
      await loadShared(shareId);
      return;
    }

    const saved = readSaved();
    const requestedYear = params.get("year");
    const knownYear = requestedYear && findYear(requestedYear);
    if (knownYear) {
      const sameYear = saved?.yearId === requestedYear;
      await openYear(requestedYear, {
        groupIds: sameYear ? saved.groupIds ?? [] : [],
        autoResult: sameYear ? saved.autoResult ?? null : null,
      });
      return;
    }
    if (saved?.yearId && findYear(saved.yearId)) {
      await openYear(saved.yearId, {
        groupIds: saved.groupIds ?? [],
        autoResult: saved.autoResult ?? null,
      });
      return;
    }
    showLanding();
  } catch (error) {
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

function readSaved() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_LAST) || "null");
    if (!parsed || typeof parsed.yearId !== "string") return null;
    const auto =
      parsed.autoResult &&
      ["partial", "relaxed"].includes(parsed.autoResult.status) &&
      Array.isArray(parsed.autoResult.excluded_subjects) &&
      Array.isArray(parsed.autoResult.requested_subject_ids)
        ? parsed.autoResult
        : null;
    return {
      yearId: parsed.yearId,
      groupIds: Array.isArray(parsed.groupIds) ? parsed.groupIds.filter((id) => typeof id === "string") : [],
      autoResult: auto,
    };
  } catch {
    return null;
  }
}

function saveLast() {
  if (!state.yearId) return;
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
        yearId: state.yearId,
        groupIds: selectedGroupIds(),
        autoResult,
        savedAt: new Date().toISOString(),
      }),
    );
  } catch {
    /* storage can be unavailable in private mode */
  }
}

function readPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(LS_PREFS) || "null");
    return {
      noFriday: Boolean(parsed?.noFriday),
      noEarly: Boolean(parsed?.noEarly),
      noLate: Boolean(parsed?.noLate),
    };
  } catch {
    return { noFriday: false, noEarly: false, noLate: false };
  }
}

function savePrefs() {
  try {
    localStorage.setItem(LS_PREFS, JSON.stringify(state.prefs));
  } catch {
    /* ignore */
  }
}

function renderPrefs() {
  if (!els.prefFriday) return;
  els.prefFriday.checked = state.prefs.noFriday;
  els.prefEarly.checked = state.prefs.noEarly;
  els.prefLate.checked = state.prefs.noLate;
}

function blockedSlots() {
  const start = state.term?.day_start ?? "08:00";
  const end = state.term?.day_end ?? "21:00";
  const slots = [];
  if (state.prefs.noFriday) slots.push({ day: "fri", start, end });
  if (state.prefs.noEarly && toMinutes(start) < 9 * 60) {
    for (const day of DAYS) slots.push({ day, start, end: "09:00" });
  }
  if (state.prefs.noLate && toMinutes(end) > 19 * 60) {
    for (const day of DAYS) slots.push({ day, start: "19:00", end });
  }
  return slots;
}

/* --------------------------------------------------------------------------
   Theme
   -------------------------------------------------------------------------- */

function applyTheme(theme, persist = true) {
  const value = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = value;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = value === "dark" ? "#101613" : "#f4f1e9";
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
   Events
   -------------------------------------------------------------------------- */

function bindEvents() {
  els.themeToggle.addEventListener("click", () => {
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  });

  els.brandLink.addEventListener("click", (event) => {
    event.preventDefault();
    if (state.yearId) showLanding();
    else window.scrollTo({ top: 0, behavior: "smooth" });
  });

  els.degreeGrid.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-year]");
    if (!button) return;
    button.disabled = true;
    try {
      await openYear(button.dataset.year, { groupIds: [] });
    } catch (error) {
      toast(error.message, { type: "error" });
      button.disabled = false;
    }
  });

  els.changeYear.addEventListener("click", showLanding);

  els.autoBtn.addEventListener("click", generateTimetable);

  for (const [key, element] of [
    ["noFriday", els.prefFriday],
    ["noEarly", els.prefEarly],
    ["noLate", els.prefLate],
  ]) {
    element.addEventListener("change", () => {
      state.prefs[key] = element.checked;
      savePrefs();
      state.autoResult = null;
      clearMove();
      state.validation = null;
      saveLast();
      renderSubjects();
      renderSchedule();
      renderStatus();
      renderAlerts();
      renderFreeSlots();
      renderMovePanel();
      scheduleValidation();
    });
  }

  els.subjects.addEventListener("click", (event) => {
    const pill = event.target.closest("[data-group-id]");
    if (!pill) return;
    const { subjectId, type, groupId } = pill.dataset;
    const current = state.selected[subjectId]?.[type];
    if (current === groupId) {
      delete state.selected[subjectId][type];
      if (!Object.keys(state.selected[subjectId]).length) delete state.selected[subjectId];
    } else {
      state.selected[subjectId] = state.selected[subjectId] || {};
      state.selected[subjectId][type] = groupId;
    }
    state.autoResult = null;
    onSelectionChanged();
  });

  els.timetable.addEventListener("click", (event) => {
    const block = event.target.closest("[data-session-id]");
    if (!block) return;
    const session = state.localSchedule.sessions.find((item) => item.id === block.dataset.sessionId);
    if (session) beginMove(session);
  });

  els.freeDuration.addEventListener("click", (event) => {
    const button = event.target.closest("[data-min]");
    if (!button) return;
    state.minFree = Number(button.dataset.min);
    updateFreeDurationButtons();
    renderFreeSlots();
  });

  els.exitMove.addEventListener("click", () => {
    clearMove();
    renderSchedule();
    renderMovePanel();
  });

  els.movePanel.addEventListener("click", (event) => {
    const candidate = event.target.closest("[data-candidate-start]");
    if (candidate) {
      state.previewCandidate = {
        day: candidate.dataset.candidateDay,
        start: candidate.dataset.candidateStart,
        end: candidate.dataset.candidateEnd,
      };
      renderSchedule();
      renderMovePanel();
      return;
    }
    if (event.target.closest("#copy-move")) {
      copyMoveProposal();
    }
  });

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
  els.themeToggle.classList.remove("hidden");
  els.degreeGrid.innerHTML = `
    <article class="degree-card">
      <h3 class="degree-name">No se pudo cargar el catálogo</h3>
      <p class="degree-meta" style="margin-top:10px">${escapeHtml(error?.message ?? "Error desconocido")}</p>
      <p class="degree-meta" style="margin-top:6px">Comprueba que el backend esté arrancado y que <code>data/catalog.json</code> exista.</p>
    </article>`;
}

function showLanding() {
  els.landing.classList.remove("hidden");
  els.workspace.classList.add("hidden");
  els.yearChip.classList.add("hidden");
  els.shareBtn.classList.add("hidden");
  const url = new URL(window.location.href);
  url.search = "";
  window.history.replaceState({}, "", url);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* --------------------------------------------------------------------------
   Opening a year
   -------------------------------------------------------------------------- */

async function openYear(yearId, { groupIds = [], autoResult = null } = {}) {
  const data = await api.year(yearId);
  state.yearData = data;
  state.term = data.term;
  state.yearId = yearId;
  state.shareId = null;
  state.autoResult = autoResult;

  buildIndexes(data);
  state.selected = {};
  state.validation = null;
  state.validationKey = null;
  clearMove();
  for (const groupId of groupIds) setGroupSelection(groupId);

  els.yearTitle.textContent = data.year.name;
  els.yearSubtitle.textContent = `${data.degree.name} · ${plural(data.subjects.length, "asignatura", "asignaturas")}`;
  els.yearChip.textContent = `${data.degree.short_name} · ${data.year.number}º`;
  els.yearChip.classList.remove("hidden");
  els.shareBtn.classList.remove("hidden");
  els.themeToggle.classList.remove("hidden");
  els.landing.classList.add("hidden");
  els.workspace.classList.remove("hidden");
  renderPrefs();

  const url = new URL(window.location.href);
  url.search = `?year=${encodeURIComponent(yearId)}`;
  window.history.replaceState({}, "", url);

  renderEverything();
  saveLast();
  scheduleValidation();
  window.scrollTo({ top: 0, behavior: "smooth" });
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
  state.selected[group.subject_id] = state.selected[group.subject_id] || {};
  state.selected[group.subject_id][group.type] = group.id;
}

function selectedGroupIds() {
  const ids = [];
  for (const subject of state.subjects) {
    const selection = state.selected[subject.id];
    if (!selection) continue;
    for (const type of Object.keys(selection).sort((a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9))) {
      if (selection[type]) ids.push(selection[type]);
    }
  }
  return ids;
}

function selectedCount() {
  return selectedGroupIds().length;
}

function selectedSubjectCount() {
  return Object.values(state.selected).filter((selection) => Object.keys(selection).length).length;
}

/* --------------------------------------------------------------------------
   Selection changes and validation
   -------------------------------------------------------------------------- */

function onSelectionChanged() {
  state.validation = null;
  state.validationKey = null;
  clearMove();
  renderEverything();
  saveLast();
  scheduleValidation();

  if (state.shareId) {
    state.shareId = null;
    const url = new URL(window.location.href);
    url.search = `?year=${encodeURIComponent(state.yearId)}`;
    window.history.replaceState({}, "", url);
  }
}

function scheduleValidation() {
  window.clearTimeout(state.validationTimer);
  state.validationTimer = window.setTimeout(refreshValidation, 170);
}

async function refreshValidation() {
  const ids = selectedGroupIds();
  const token = ++state.validationToken;
  if (!ids.length) {
    state.validation = null;
    state.validationKey = ids.join(",");
    renderAlerts();
    renderFreeSlots();
    renderStatus();
    return;
  }
  try {
    const result = await api.validate({
      group_ids: ids,
      blocked: blockedSlots(),
      min_free_minutes: 30,
    });
    if (token !== state.validationToken) return;
    if (ids.join(",") !== selectedGroupIds().join(",")) return;
    state.validation = result;
    state.validationKey = ids.join(",");
    renderAlerts();
    renderFreeSlots();
    renderStatus();
  } catch (error) {
    if (token !== state.validationToken) return;
    toast(`No se pudo validar el horario: ${error.message}`, { type: "error" });
  }
}

/* --------------------------------------------------------------------------
   Schedule computation (client-side, instant)
   -------------------------------------------------------------------------- */

function buildLocalSchedule() {
  const sessions = [];
  const blocked = blockedSlots();
  for (const groupId of selectedGroupIds()) {
    const group = state.groupIndex.get(groupId);
    if (!group) continue;
    for (const session of group.sessions) {
      const prefersConflict = blocked.some(
        (slot) =>
          session.day === slot.day &&
          toMinutes(session.start) < toMinutes(slot.end) &&
          toMinutes(slot.start) < toMinutes(session.end),
      );
      sessions.push({
        ...session,
        has_conflict: false,
        has_pref_conflict: prefersConflict,
        conflict_with: [],
      });
    }
  }
  sessions.sort(
    (a, b) =>
      (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) ||
      toMinutes(a.start) - toMinutes(b.start) ||
      a.subject_name.localeCompare(b.subject_name, "es"),
  );

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
  return { sessions, conflicts };
}

function minutesToHm(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

function sessionsOverlap(a, b) {
  if (a.day !== b.day) return false;
  return toMinutes(a.start) < toMinutes(b.end) && toMinutes(b.start) < toMinutes(a.end);
}

function groupConflicts(group) {
  const candidateSessions = group.sessions;
  for (const selectedId of selectedGroupIds()) {
    const selectedGroup = state.groupIndex.get(selectedId);
    if (!selectedGroup) continue;
    if (selectedGroup.subject_id === group.subject_id && selectedGroup.type === group.type) continue;
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
  renderSubjects();
  renderSchedule();
  renderStatus();
  renderAlerts();
  renderFreeSlots();
  renderMovePanel();
}

function renderSubjects() {
  if (!state.subjects.length) return;
  const scrollTop = els.subjects.scrollTop;
  els.subjects.innerHTML = state.subjects
    .map((subject) => {
      const selection = state.selected[subject.id] || {};
      const types = [...new Set(subject.groups.map((group) => group.type))].sort(
        (a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9),
      );
      const selectedTypes = types.filter((type) => selection[type]);
      const rows = types
        .map((type) => {
          const groups = subject.groups
            .filter((group) => group.type === type)
            .sort((a, b) => a.code.localeCompare(b.code, "es", { numeric: true }));
          const typeLabel = groups[0]?.type_label ?? type;
          const missing = !selection[type];
          const pills = groups
            .map((group) => {
              const selected = selection[type] === group.id;
              const conflict = groupConflicts(group);
              const pref = groupHitsPreferences(group);
              const classes = [
                "group-pill",
                selected ? "is-selected" : "",
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
        .map((type) => state.groupIndex.get(selection[type]))
        .filter(Boolean)
        .reduce((sum, group) => sum + group.weekly_minutes, 0);
      return `
        <article class="subject-card ${selectedTypes.length ? "has-selection" : ""}" style="--subject-color:${subject.color}">
          <header class="subject-head">
            <span class="subject-dot" aria-hidden="true"></span>
            <div class="subject-title">
              <span class="subject-name">${escapeHtml(subject.name)}</span>
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
  const overlay = mergeCandidateRanges(state.moveCandidates);
  renderTimetable(els.timetable, {
    sessions: schedule.sessions,
    term: state.term,
    candidateSlots: state.moveSession ? overlay : [],
    movingSessionId: state.moveSession?.id ?? null,
    activeCandidate: state.previewCandidate,
  });
}

function renderStatus() {
  const { sessions, conflicts } = state.localSchedule;
  const blocked = localBlockedConflicts(sessions);
  const missing = missingGroups();
  const groupCount = selectedCount();
  let label = "Sin grupos";
  let className = "status-pill is-empty";

  if (groupCount) {
    if (conflicts.length) {
      label = plural(conflicts.length, "solape", "solapes");
      className = "status-pill is-danger";
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
  for (const subject of state.subjects) {
    if (deliberatelyExcluded.has(subject.id)) continue;
    const selection = state.selected[subject.id] || {};
    const types = [...new Set(subject.groups.map((group) => group.type))];
    for (const type of types) {
      if (!selection[type]) {
        const group = subject.groups.find((item) => item.type === type);
        missing.push({ subject, type, typeLabel: group?.type_label ?? type });
      }
    }
  }
  return missing;
}

function renderAlerts() {
  const alerts = [];
  const { conflicts, sessions } = state.localSchedule;
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
   Rendering — free slots
   -------------------------------------------------------------------------- */

function updateFreeDurationButtons() {
  for (const button of els.freeDuration.querySelectorAll("[data-min]")) {
    button.classList.toggle("is-active", Number(button.dataset.min) === state.minFree);
  }
}

function renderFreeSlots() {
  if (!selectedCount()) {
    els.freeSlots.innerHTML = '<p class="empty-copy">Elige algún grupo para ver tus huecos.</p>';
    return;
  }
  if (!state.validation) {
    els.freeSlots.innerHTML = '<p class="empty-copy">Calculando huecos…</p>';
    return;
  }
  const slots = (state.validation.free_slots || []).filter((slot) => slot.duration_minutes >= state.minFree);
  if (!slots.length) {
    els.freeSlots.innerHTML = `<p class="empty-copy">No hay huecos de ${formatDuration(state.minFree)} o más. Prueba con una duración menor o cambia algún grupo.</p>`;
    return;
  }
  const byDay = new Map(DAYS.map((day) => [day, []]));
  for (const slot of slots) byDay.get(slot.day)?.push(slot);
  els.freeSlots.innerHTML = DAYS.filter((day) => byDay.get(day).length)
    .map(
      (day) => `
      <div class="free-day">
        <span class="free-day-name">${DAY_LABELS[day].slice(0, 3)}</span>
        <span class="free-day-chips">
          ${byDay
            .get(day)
            .map(
              (slot) =>
                `<span class="free-chip">${slot.start}–${slot.end}<small>${formatDuration(slot.duration_minutes)}</small></span>`,
            )
            .join("")}
        </span>
      </div>`,
    )
    .join("");
}

/* --------------------------------------------------------------------------
   Rendering — move mode
   -------------------------------------------------------------------------- */

function beginMove(session) {
  state.moveSession = session;
  state.previewCandidate = null;
  state.moveCandidates = [];
  state.moveLoading = true;
  renderSchedule();
  renderMovePanel();
  els.exitMove.classList.remove("hidden");
  if (window.innerWidth < 980) {
    els.moveCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  computeMoveCandidates(session);
}

function clearMove() {
  state.moveSession = null;
  state.moveCandidates = [];
  state.previewCandidate = null;
  state.moveLoading = false;
  state.moveToken += 1;
  if (els.exitMove) els.exitMove.classList.add("hidden");
}

async function computeMoveCandidates(session) {
  const token = ++state.moveToken;
  const ids = selectedGroupIds();
  if (!ids.includes(session.group_id)) ids.push(session.group_id);
  try {
    const [personal, groupFree] = await Promise.all([
      api.freeSlots({
        group_ids: ids,
        ignore_session_ids: [session.id],
        duration_minutes: session.duration_minutes,
        blocked: blockedSlots(),
      }),
      api.freeSlots({
        group_ids: [session.group_id],
        ignore_session_ids: [session.id],
        duration_minutes: session.duration_minutes,
        blocked: blockedSlots(),
      }),
    ]);
    if (token !== state.moveToken || state.moveSession?.id !== session.id) return;
    const common = intersectIntervals(personal.slots, groupFree.slots);
    const candidates = expandCandidates(common, session.duration_minutes, session);
    state.moveCandidates = candidates;
    state.moveLoading = false;
    renderSchedule();
    renderMovePanel();
  } catch (error) {
    if (token !== state.moveToken) return;
    state.moveLoading = false;
    state.moveCandidates = [];
    renderMovePanel();
    toast(`No se pudieron buscar huecos: ${error.message}`, { type: "error" });
  }
}

function intersectIntervals(first, second) {
  const result = [];
  for (const a of first) {
    for (const b of second) {
      if (a.day !== b.day) continue;
      const start = Math.max(toMinutes(a.start), toMinutes(b.start));
      const end = Math.min(toMinutes(a.end), toMinutes(b.end));
      if (end > start) {
        result.push({
          day: a.day,
          start: minutesToHm(start),
          end: minutesToHm(end),
          duration_minutes: end - start,
        });
      }
    }
  }
  return result;
}

function expandCandidates(intervals, duration, current) {
  const candidates = [];
  const seen = new Set();
  for (const interval of intervals) {
    let cursor = toMinutes(interval.start);
    const end = toMinutes(interval.end);
    while (cursor + duration <= end) {
      const start = minutesToHm(cursor);
      const key = `${interval.day}-${start}`;
      if (!seen.has(key) && !(interval.day === current.day && start === current.start)) {
        seen.add(key);
        candidates.push({
          day: interval.day,
          day_label: DAY_LABELS[interval.day],
          start,
          end: minutesToHm(cursor + duration),
          duration_minutes: duration,
        });
      }
      cursor += 30;
    }
  }
  return candidates.sort(
    (a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start),
  );
}

function mergeCandidateRanges(candidates) {
  const byDay = new Map();
  for (const candidate of candidates) {
    if (!byDay.has(candidate.day)) byDay.set(candidate.day, []);
    byDay.get(candidate.day).push({ start: toMinutes(candidate.start), end: toMinutes(candidate.end) });
  }
  const result = [];
  for (const [day, intervals] of byDay) {
    intervals.sort((a, b) => a.start - b.start);
    const merged = [];
    for (const interval of intervals) {
      const last = merged[merged.length - 1];
      if (last && interval.start <= last.end) last.end = Math.max(last.end, interval.end);
      else merged.push({ ...interval });
    }
    for (const interval of merged) {
      result.push({
        day,
        start: minutesToHm(interval.start),
        end: minutesToHm(interval.end),
        duration_minutes: interval.end - interval.start,
      });
    }
  }
  return result;
}

function renderMovePanel() {
  const session = state.moveSession;
  if (!session) {
    els.exitMove.classList.add("hidden");
    els.movePanel.innerHTML =
      '<p class="empty-copy">Haz clic en cualquier clase del calendario para ver dónde podría encajar sin solaparse con el resto de tu semana.</p>';
    return;
  }
  els.exitMove.classList.remove("hidden");
  const header = `
    <div class="move-current" style="--subject-color:${session.color}">
      <span class="move-current-dot" style="background:${session.color}" aria-hidden="true"></span>
      <div class="move-current-main">
        <strong>${escapeHtml(session.subject_name)}</strong>
        <small>${escapeHtml(session.day_label)} ${session.start}–${session.end} · ${escapeHtml(session.group_code)} · ${escapeHtml(session.room_name)}</small>
      </div>
    </div>`;

  if (state.moveLoading) {
    els.movePanel.innerHTML = `${header}<p class="move-copy">Buscando huecos compatibles con el grupo y con tu semana…</p>`;
    return;
  }
  if (!state.moveCandidates.length) {
    els.movePanel.innerHTML = `${header}<p class="move-copy">No hay ningún hueco compatible de ${formatDuration(session.duration_minutes)}. Prueba a desactivar alguna preferencia o a quitar otra clase.</p>`;
    return;
  }

  const visible = state.moveCandidates.slice(0, 12);
  const chips = visible
    .map((candidate) => {
      const active =
        state.previewCandidate?.day === candidate.day && state.previewCandidate?.start === candidate.start
          ? "is-active"
          : "";
      return `<button class="candidate-chip ${active}" type="button"
        data-candidate-day="${candidate.day}"
        data-candidate-start="${candidate.start}"
        data-candidate-end="${candidate.end}">${escapeHtml(DAY_LABELS[candidate.day].slice(0, 3))} ${candidate.start}–${candidate.end}</button>`;
    })
    .join("");
  els.movePanel.innerHTML = `
    ${header}
    <p class="move-copy">${plural(state.moveCandidates.length, "hueco compatible", "huecos compatibles")} para este grupo. Elige uno para verlo en el calendario.</p>
    <div class="candidate-list">${chips}</div>
    ${state.moveCandidates.length > visible.length ? `<p class="move-more">y ${state.moveCandidates.length - visible.length} más</p>` : ""}
    <div class="move-actions">
      <button class="btn btn-accent" type="button" id="copy-move">Copiar propuesta</button>
    </div>`;
}

async function copyMoveProposal() {
  const session = state.moveSession;
  const candidate = state.previewCandidate || state.moveCandidates[0];
  if (!session || !candidate) return;
  const text = `${session.subject_name} (${session.group_code}): mover de ${session.day_label} ${session.start}–${session.end} a ${DAY_LABELS[candidate.day]} ${candidate.start}–${candidate.end}. El hueco está libre para el grupo y para el resto de tu horario.`;
  await copyText(text);
}

/* --------------------------------------------------------------------------
   Automatic generation
   -------------------------------------------------------------------------- */

async function generateTimetable() {
  if (!state.yearId) return;
  els.autoBtn.disabled = true;
  els.autoBtn.querySelector(".spinner").classList.remove("hidden");
  els.autoBtn.querySelector(".btn-label").style.opacity = "0.55";
  els.autoHint.textContent = "Explorando combinaciones…";
  try {
    const result = await api.generate({
      year_id: state.yearId,
      blocked: blockedSlots(),
      allow_partial: true,
      allow_conflicts: true,
    });
    state.autoResult = result;
    state.selected = {};
    for (const groupId of result.group_ids || []) setGroupSelection(groupId);
    state.shareId = null;
    state.validation = result;
    state.validationKey = (result.group_ids || []).join(",");
    state.moveSession = null;
    state.moveCandidates = [];
    state.previewCandidate = null;
    els.exitMove.classList.add("hidden");

    const url = new URL(window.location.href);
    url.search = `?year=${encodeURIComponent(state.yearId)}`;
    window.history.replaceState({}, "", url);

    renderEverything();
    saveLast();

    if (result.status === "optimal") {
      toast(`Horario generado con ${plural(result.group_ids.length, "grupo", "grupos")} y sin solapes.`);
    } else if (result.status === "partial") {
      const total = result.requested_subject_ids?.length ?? 0;
      const included = total - (result.excluded_subjects?.length ?? 0);
      toast(`Horario generado con ${included} de ${total} materias. Las que no caben aparecen en el aviso.`, {
        type: "warn",
        duration: 6000,
      });
    } else if (result.status === "relaxed") {
      toast("No hay una combinación perfecta con esas preferencias; revisa los solapes marcados.", {
        type: "warn",
        duration: 6000,
      });
    } else {
      toast("No se encontró ninguna combinación posible.", { type: "error" });
    }
  } catch (error) {
    toast(`No se pudo generar el horario: ${error.message}`, { type: "error" });
  } finally {
    els.autoBtn.disabled = false;
    els.autoBtn.querySelector(".spinner").classList.add("hidden");
    els.autoBtn.querySelector(".btn-label").style.opacity = "1";
    els.autoHint.textContent = "Si hay optativas incompatibles, elegimos el mayor subconjunto posible.";
  }
}

/* --------------------------------------------------------------------------
   Share, export, toast
   -------------------------------------------------------------------------- */

async function loadShared(shareId) {
  try {
    const response = await api.shared(shareId);
    const share = response.share;
    await openYear(share.year_id, { groupIds: share.group_ids });
    state.shareId = share.id;
    state.validation = response.validation;
    state.validationKey = (response.validation.group_ids || []).join(",");
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
  const ids = selectedGroupIds();
  if (!ids.length) {
    toast("Elige algún grupo antes de compartir.", { type: "warn" });
    return;
  }
  els.shareBtn.disabled = true;
  try {
    const response = await api.share({ year_id: state.yearId, group_ids: ids });
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
  const payload = {
    schema_version: "2.0",
    exported_at: new Date().toISOString(),
    term: state.yearData?.term,
    degree: state.yearData?.degree,
    year: state.yearData?.year,
    group_ids: selectedGroupIds(),
    selections: state.subjects
      .map((subject) => {
        const selection = state.selected[subject.id] || {};
        return {
          subject_id: subject.id,
          subject_name: subject.name,
          groups: Object.entries(selection).map(([type, groupId]) => {
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
          }),
        };
      })
      .filter((subject) => subject.groups.length),
    stats: state.validation?.stats ?? null,
  };
  downloadBlob(
    JSON.stringify(payload, null, 2),
    `horario-${slugify(state.yearData?.degree?.short_name ?? "usc")}-${state.yearData?.year?.number ?? ""}.json`,
    "application/json",
  );
  toast("JSON descargado.");
}

function downloadIcs() {
  const ids = selectedGroupIds();
  if (!ids.length) return;
  const url = new URL("/api/schedules/export.ics", window.location.origin);
  url.searchParams.set("group_ids", ids.join(","));
  url.searchParams.set(
    "name",
    `Horario ${state.yearData?.degree?.short_name ?? "USC"} ${state.yearData?.year?.number ?? ""}º`,
  );
  const anchor = document.createElement("a");
  anchor.href = url.toString();
  anchor.download = "horario.ics";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  toast("Calendario .ics descargado.");
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
  const blob = new Blob([content], { type });
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
