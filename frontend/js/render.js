/**
 * Rendering of the workspace ("Mi horario") from ``state``.
 *
 * ``renderEverything()`` redraws it all; the other functions redraw one
 * panel and are used when only that part changes.
 */

import { DAY_LABELS, GROUP_TYPE_ORDER, SEMESTER_NAMES } from "./constants.js";
import { renderEventList } from "./events.js";
import { resolveButton, resolvedAlert } from "./resolve.js";
import { buildLocalSchedule, groupConflicts, upcomingClasses } from "./schedule.js";
import { isPinnedGroup, missingGroups, selectedCount, selectedSubjectCount, semesterSubjects } from "./selection.js";
import { els, state } from "./state.js";
import { renderTimetable } from "./timetable.js";
import { escapeHtml, formatHours, plural, safeEventColor } from "./utils.js";
import { activeWeekId, currentWeekId, formatWeekDate, inWeek, weekPatterns } from "./weeks.js";

export function renderEverything() {
  renderUpcoming();
  renderWeekSelect();
  renderSubjects();
  renderSchedule();
  renderStatus();
  renderAlerts();
  renderEventList();
}

/* --------------------------------------------------------------------------
   Sidebar: subjects and their groups
   -------------------------------------------------------------------------- */

function groupPill(subject, type, group, selection) {
  const selected = Boolean(selection[type]?.includes(group.id));
  const pinned = isPinnedGroup(group.id);
  const conflict = groupConflicts(group);
  const classes = ["group-pill", selected ? "is-selected" : "", pinned ? "is-pinned" : "", conflict ? "is-conflict" : ""]
    .filter(Boolean)
    .join(" ");
  const tooltip = [
    `${group.code} · ${group.type_label}`,
    ...group.sessions.map(
      (session) =>
        `${DAY_LABELS[session.day]} ${session.start}–${session.end} · ${session.room_name} (${session.building_name})`,
    ),
    conflict ? "Solapa con la selección actual" : "",
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
}

function groupTypeRow(subject, type, selection) {
  const groups = subject.groups
    .filter((group) => group.type === type)
    .sort((a, b) => a.code.localeCompare(b.code, "es", { numeric: true }));
  const typeLabel = groups[0]?.type_label ?? type;
  const missing = !selection[type]?.length;
  return `
    <div class="group-type">
      <div class="group-type-head">
        <span>${escapeHtml(typeLabel)}</span>
        ${missing ? '<span class="missing">sin elegir</span>' : ""}
      </div>
      <div class="group-options">${groups.map((group) => groupPill(subject, type, group, selection)).join("")}</div>
    </div>`;
}

function subjectCard(subject, showOrigin) {
  const selection = state.selected[subject.id] || {};
  const types = [...new Set(subject.groups.map((group) => group.type))].sort(
    (a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9),
  );
  const selectedTypes = types.filter((type) => selection[type]?.length);
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
      ${types.map((type) => groupTypeRow(subject, type, selection)).join("")}
    </article>`;
}

export function renderSubjects() {
  if (!state.subjects.length) return;
  const scrollTop = els.subjects.scrollTop;
  const showOrigin = (state.yearData?.years?.length ?? 0) > 1 || (state.yearData?.degrees?.length ?? 0) > 1;
  const subjects = semesterSubjects();
  if (!subjects.length) {
    els.subjects.innerHTML = `<p class="empty-copy">Tu matrícula no tiene asignaturas del ${SEMESTER_NAMES[state.semester]}. Cambia de cuatrimestre arriba o edita la matrícula.</p>`;
    return;
  }
  els.subjects.innerHTML = subjects.map((subject) => subjectCard(subject, showOrigin)).join("");
  els.subjects.scrollTop = scrollTop;
}

/* --------------------------------------------------------------------------
   Timetable, status bar and week selector
   -------------------------------------------------------------------------- */

// Activities of the week on screen, marked when they clash with a class.
export function weekEvents(schedule = state.localSchedule) {
  const conflictEventIds = new Set(schedule.eventConflicts.map((conflict) => conflict.event.id));
  const weekId = activeWeekId();
  return state.customEvents.filter((event) => inWeek(event, weekId)).map((event) => ({
    ...event,
    color: safeEventColor(event.color),
    has_event_conflict: conflictEventIds.has(event.id),
  }));
}

export function renderSchedule() {
  if (!state.term) return;
  const schedule = buildLocalSchedule();
  state.localSchedule = schedule;
  renderTimetable(els.timetable, { sessions: schedule.sessions, events: weekEvents(schedule), term: state.term });
}

function statusLabel(conflicts, eventConflicts, missing) {
  if (conflicts.length) return [plural(conflicts.length, "solape", "solapes"), "status-pill is-danger"];
  if (eventConflicts.length) {
    return [`${plural(eventConflicts.length, "cruce", "cruces")} con extraescolares`, "status-pill is-warn"];
  }
  if (missing.length) {
    return [`${plural(missing.length, "grupo pendiente", "grupos pendientes")}`, "status-pill is-warn"];
  }
  if (state.autoResult?.status === "partial") {
    const total = state.autoResult.requested_subject_ids?.length ?? 0;
    const included = total - (state.autoResult.excluded_subjects?.length ?? 0);
    return [`${included} de ${total} materias`, "status-pill"];
  }
  return ["Sin solapes", "status-pill"];
}

export function renderStatus() {
  const { sessions, conflicts, eventConflicts } = state.localSchedule;
  const groupCount = selectedCount();
  const [label, className] = groupCount
    ? statusLabel(conflicts, eventConflicts, missingGroups())
    : ["Sin grupos", "status-pill is-empty"];
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

export function renderWeekSelect() {
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
  els.weekPicker.title =
    current && !current.usual ? `Semanas: ${current.weeks.map(formatWeekDate).join(", ")}` : "Semana que se muestra";
}

/* --------------------------------------------------------------------------
   Alerts above the timetable
   -------------------------------------------------------------------------- */

function alertBox(kind, icon, content) {
  return `
    <div class="alert alert-${kind}">
      <span class="alert-icon">${icon}</span>
      <div>${content}</div>
    </div>`;
}

function conflictAlert(conflicts) {
  const items = conflicts
    .slice(0, 4)
    .map(
      (conflict) =>
        `<li>${escapeHtml(conflict.left.subject_name)} (${escapeHtml(conflict.left.group_code)}) × ${escapeHtml(conflict.right.subject_name)} (${escapeHtml(conflict.right.group_code)}) · ${escapeHtml(conflict.day_label)} ${conflict.start}–${conflict.end}</li>`,
    );
  return alertBox(
    "danger",
    "!",
    `<strong>Hay ${plural(conflicts.length, "solape", "solapes")} en tu semana.</strong>
     <ul>${items.join("")}</ul>${resolveButton()}`,
  );
}

function eventConflictAlert(eventConflicts) {
  const items = eventConflicts
    .slice(0, 4)
    .map(
      (conflict) =>
        `<li>${escapeHtml(conflict.event.title)} × ${escapeHtml(conflict.session.subject_name)} · ${escapeHtml(conflict.day_label)} ${conflict.start}–${conflict.end}</li>`,
    );
  return alertBox(
    "warn",
    "⚑",
    `<strong>Hay ${plural(eventConflicts.length, "cruce", "cruces")} con actividades extraescolares.</strong>
     <ul>${items.join("")}</ul>`,
  );
}

function missingAlert(missing) {
  const visible = missing.slice(0, 5);
  const items = visible.map((item) => `<li>${escapeHtml(item.subject.name)} · ${escapeHtml(item.typeLabel)}</li>`);
  const more = missing.length > visible.length ? `<li>y ${missing.length - visible.length} más</li>` : "";
  return alertBox(
    "warn",
    "○",
    `<strong>Te falta elegir ${plural(missing.length, "grupo", "grupos")}.</strong>
     <ul>${items.join("")}${more}</ul>`,
  );
}

// Messages of the old automatic generator, still shown for saved plans.
function autoResultAlerts(conflicts) {
  const alerts = [];
  const auto = state.autoResult;
  if (auto?.status === "partial" && auto.excluded_subjects?.length) {
    const total = auto.requested_subject_ids?.length ?? 0;
    const included = total - auto.excluded_subjects.length;
    const names = auto.excluded_subjects.map((subject) => `<li>${escapeHtml(subject.name)}</li>`).join("");
    alerts.push(
      alertBox(
        "info",
        "✦",
        `<strong>El horario automático encaja ${included} de ${total} materias.</strong>
         No caben sin solapes: <ul>${names}</ul>Añádelas a mano si quieres asumir el solape.`,
      ),
    );
  }
  if (auto?.status === "relaxed" && conflicts.length) {
    alerts.push(
      alertBox(
        "warn",
        "≈",
        `<strong>No existe una combinación sin solapes con esas preferencias.</strong>
         Te mostramos la mejor aproximación; prueba a quitar «Sin viernes» o «Sin 8:00».`,
      ),
    );
  }
  return alerts;
}

// Only the overlaps of the week on screen are shown (decided by the user).
export function renderAlerts() {
  const { conflicts, eventConflicts } = state.localSchedule;
  const missing = missingGroups();
  const alerts = [
    resolvedAlert(),
    conflicts.length ? conflictAlert(conflicts) : "",
    eventConflicts.length ? eventConflictAlert(eventConflicts) : "",
    missing.length ? missingAlert(missing) : "",
    ...autoResultAlerts(conflicts),
  ];
  els.alerts.innerHTML = alerts.filter(Boolean).join("");
}

/* --------------------------------------------------------------------------
   "Próximamente"
   -------------------------------------------------------------------------- */

export function renderUpcoming() {
  const upcoming = state.planSubjectIds.length ? upcomingClasses() : null;
  els.upcoming.classList.toggle("hidden", !upcoming);
  if (!upcoming) {
    els.upcoming.innerHTML = "";
    return;
  }
  const wait = upcoming.minutes === 1 ? "en 1 minuto" : `en ${upcoming.minutes} min`;
  const blocks = upcoming.blocks
    .map(
      (block) => `
      <div class="upcoming-block">
        <span class="tt-block-name">${escapeHtml(block.subject_name)}</span>
        <span class="tt-block-meta">${escapeHtml([block.group_code, block.group_type_label, block.room_name].filter(Boolean).join(" · "))}</span>
        <span class="upcoming-foot">
          <span class="upcoming-time">${block.start}–${block.end}</span>
          <span class="tt-block-building">${escapeHtml(block.building_short_name || block.building_name || "")} 📍</span>
        </span>
      </div>`,
    )
    .join("");
  els.upcoming.innerHTML = `
    <p class="upcoming-kicker">Próximamente <span>· ${wait}</span></p>
    ${blocks}`;
}
