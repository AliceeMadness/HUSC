/**
 * Extracurricular activities ("Extraescolares"): dialog, list and validation
 * of activities restored from saved plans or shared links.
 *
 * An activity happens every week (``weeks: null``) or only in the ISO weeks
 * listed in ``weeks``, like the classes of the catalogue.
 */

import { DAY_LABELS, DAY_ORDER, SEMESTER_NAMES } from "./constants.js";
import { renderEverything } from "./render.js";
import { els, state } from "./state.js";
import { saveLast } from "./storage.js";
import { toMinutes } from "./timetable.js";
import { toast } from "./ui.js";
import { escapeHtml, isTime, plural, safeEventColor } from "./utils.js";
import { scheduleValidation } from "./validation.js";
import { currentWeekId, formatWeekDate } from "./weeks.js";

const MAX_EVENTS = 80;
const WEEK_ID = /^\d{4}-W\d{2}$/;

// ``null`` (every week) or a sorted list of distinct ISO weeks.
function cleanWeeks(weeks) {
  if (!Array.isArray(weeks)) return null;
  const valid = [...new Set(weeks.filter((week) => typeof week === "string" && WEEK_ID.test(week)))].sort();
  return valid.length ? valid : null;
}

export function eventWeeksLabel(event) {
  return event.weeks ? plural(event.weeks.length, "semana", "semanas") : "todas las semanas";
}

// Activities from localStorage or a shared link, keeping only valid ones.
export function normalizeIncomingEvents(events) {
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
      weeks: cleanWeeks(event.weeks),
    }))
    .slice(0, MAX_EVENTS);
}

/* --------------------------------------------------------------------------
   Week picker of the dialog
   -------------------------------------------------------------------------- */

const capitalize = (text) => text.charAt(0).toUpperCase() + text.slice(1);

function renderWeekPicker(selected) {
  const calendar = state.term?.calendar ?? [];
  // Catalogues with a single typical week have no weeks to choose.
  els.eventWeeksField.classList.toggle("hidden", !calendar.length);
  const today = currentWeekId();
  const terms = [...new Set(calendar.map((week) => week.semester))];
  els.eventWeeks.innerHTML = terms
    .map((semester) => {
      const chips = calendar
        .filter((week) => week.semester === semester)
        .map((week) => {
          const isToday = week.id === today;
          const date = formatWeekDate(week.id);
          return `
          <label class="week-chip${isToday ? " is-today" : ""}" title="Semana del ${escapeHtml(date)}${isToday ? " (esta semana)" : ""}">
            <input type="checkbox" value="${escapeHtml(week.id)}" data-event-week ${selected.has(week.id) ? "checked" : ""}>
            <span>${escapeHtml(date)}</span>
          </label>`;
        })
        .join("");
      return `
        <div class="event-weeks-term">
          <div class="event-weeks-head">
            <strong>${escapeHtml(capitalize(SEMESTER_NAMES[semester] ?? `${semester}º cuatrimestre`))}</strong>
            <button class="text-btn" type="button" data-weeks-term="${semester}">Todo / nada</button>
          </div>
          <div class="event-weeks-grid">${chips}</div>
        </div>`;
    })
    .join("");
}

function weeksMode() {
  return els.eventDialog.querySelector('input[name="event-weeks-mode"]:checked')?.value ?? "all";
}

function setWeeksMode(mode) {
  els.eventDialog.querySelector(`input[name="event-weeks-mode"][value="${mode}"]`).checked = true;
  els.eventWeeks.classList.toggle("hidden", mode !== "some");
}

function chosenWeeks() {
  return [...els.eventWeeks.querySelectorAll("[data-event-week]:checked")].map((input) => input.value).sort();
}

// Tick every week of a term, or untick them if all were ticked.
function toggleTermWeeks(semester) {
  const ids = new Set(
    (state.term?.calendar ?? []).filter((week) => String(week.semester) === semester).map((week) => week.id),
  );
  const inputs = [...els.eventWeeks.querySelectorAll("[data-event-week]")].filter((input) => ids.has(input.value));
  const allChecked = inputs.every((input) => input.checked);
  for (const input of inputs) input.checked = !allChecked;
}

export function bindEventDialog() {
  for (const radio of els.eventDialog.querySelectorAll('input[name="event-weeks-mode"]')) {
    radio.addEventListener("change", () => setWeeksMode(weeksMode()));
  }
  els.eventWeeks.addEventListener("click", (event) => {
    const button = event.target.closest("[data-weeks-term]");
    if (button) toggleTermWeeks(button.dataset.weeksTerm);
  });
}

export function openEventDialog(eventId = null) {
  const event = eventId ? state.customEvents.find((item) => item.id === eventId) : null;
  const values = event ?? {};
  state.editingEventId = event?.id ?? null;
  els.eventDialogTitle.textContent = event ? "Editar extraescolar" : "Añadir extraescolar";
  els.eventTitle.value = values.title ?? "";
  els.eventDay.value = values.day ?? "mon";
  els.eventStart.value = values.start ?? "18:00";
  els.eventEnd.value = values.end ?? "20:00";
  els.eventLocation.value = values.location ?? "";
  renderWeekPicker(new Set(values.weeks ?? []));
  setWeeksMode(values.weeks ? "some" : "all");
  const color = safeEventColor(values.color);
  const colorInput = els.eventDialog.querySelector(`input[name="event-color"][value="${color}"]`);
  if (colorInput) colorInput.checked = true;
  else els.eventDialog.querySelector('input[name="event-color"]').checked = true;
  els.eventDelete.classList.toggle("hidden", !event);
  els.eventDialog.showModal();
  window.setTimeout(() => els.eventTitle.focus(), 50);
}

// The reason the form cannot be saved, or null.
function formProblem(title, start, end, weeks) {
  if (!title) return "Ponle un título a la extraescolar.";
  if (weeks && !weeks.length) return "Elige al menos una semana o marca «Todas las semanas».";
  if (!isTime(start) || !isTime(end) || toMinutes(end) <= toMinutes(start)) {
    return "El horario de la extraescolar no es válido.";
  }
  const { day_start: dayStart, day_end: dayEnd } = state.term;
  if (toMinutes(start) < toMinutes(dayStart) || toMinutes(end) > toMinutes(dayEnd)) {
    return `La extraescolar debe estar entre las ${dayStart} y las ${dayEnd}.`;
  }
  return null;
}

// Redraw and revalidate after any change to the activities.
function afterEventsChanged(message) {
  state.editingEventId = null;
  els.eventDialog.close();
  state.autoResult = null;
  state.validation = null;
  renderEverything();
  saveLast();
  scheduleValidation();
  toast(message);
}

export function saveEventForm(event) {
  event.preventDefault();
  const title = els.eventTitle.value.trim();
  const start = els.eventStart.value;
  const end = els.eventEnd.value;
  const pickingWeeks = weeksMode() === "some" && !els.eventWeeksField.classList.contains("hidden");
  const weeks = pickingWeeks ? chosenWeeks() : null;
  const problem = formProblem(title, start, end, weeks);
  if (problem) {
    toast(problem, { type: "warn" });
    return;
  }
  const selectedColor = els.eventDialog.querySelector('input[name="event-color"]:checked');
  const id =
    state.editingEventId || `evt-${globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2)}`;
  const item = {
    id,
    title,
    day: els.eventDay.value,
    start,
    end,
    location: els.eventLocation.value.trim() || null,
    color: safeEventColor(selectedColor?.value),
    kind: "extracurricular",
    weeks,
  };
  const index = state.customEvents.findIndex((current) => current.id === id);
  if (index === -1) state.customEvents.push(item);
  else state.customEvents[index] = item;
  state.customEvents.sort(
    (a, b) => (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start),
  );
  afterEventsChanged(index === -1 ? "Extraescolar añadida." : "Extraescolar actualizada.");
}

export function deleteEditingEvent() {
  if (!state.editingEventId) return;
  state.customEvents = state.customEvents.filter((event) => event.id !== state.editingEventId);
  afterEventsChanged("Extraescolar eliminada.");
}

export function renderEventList() {
  if (!state.customEvents.length) {
    els.eventList.innerHTML = `
      <p class="event-empty">Añade entrenamientos, trabajo o academias con «Extraescolar» para que el horario los respete.</p>`;
    return;
  }
  const chips = state.customEvents
    .map((event) => {
      const where = event.location ? ` · ${event.location}` : "";
      const some = event.weeks ? ` · ${eventWeeksLabel(event)}` : "";
      return `
      <button class="event-chip" type="button" data-event-id="${escapeHtml(event.id)}"
        style="--event-color:${safeEventColor(event.color)}"
        title="${escapeHtml(`${event.title} · ${DAY_LABELS[event.day]} ${event.start}–${event.end}${where} · ${eventWeeksLabel(event)}`)}">
        <span class="event-chip-dot" aria-hidden="true"></span>
        <span>${escapeHtml(event.title)}</span>
        <small>${escapeHtml(DAY_LABELS[event.day].slice(0, 3))} ${event.start}–${event.end}${escapeHtml(some)}</small>
      </button>`;
    })
    .join("");
  els.eventList.innerHTML = `<div class="event-chips">${chips}</div>`;
}
