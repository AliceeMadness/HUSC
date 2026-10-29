/**
 * Weekly timetable renderer.
 *
 * Blocks are absolutely positioned inside each day column, with all geometry
 * expressed as CSS custom properties in minutes.  Overlapping blocks are split
 * into columns so conflicts remain readable instead of being hidden.
 */

const DAYS = ["mon", "tue", "wed", "thu", "fri"];
const DAY_LABELS = { mon: "Lunes", tue: "Martes", wed: "Miércoles", thu: "Jueves", fri: "Viernes" };

function toMinutes(value) {
  const [hours, minutes] = value.split(":").map(Number);
  return hours * 60 + minutes;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDuration(minutes) {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest}′`;
  if (!rest) return `${hours} h`;
  return `${hours} h ${rest}′`;
}

function layoutDay(events) {
  const sorted = [...events].sort(
    (a, b) => toMinutes(a.start) - toMinutes(b.start) || toMinutes(a.end) - toMinutes(b.end),
  );
  const clusters = [];
  let current = null;
  let clusterEnd = -1;

  for (const event of sorted) {
    const start = toMinutes(event.start);
    const end = toMinutes(event.end);
    if (!current || start >= clusterEnd) {
      current = { events: [], end: 0 };
      clusters.push(current);
      clusterEnd = -1;
    }
    current.events.push({ ...event, _start: start, _end: end });
    clusterEnd = Math.max(clusterEnd, end);
    current.end = Math.max(current.end, end);
  }

  const placed = [];
  for (const cluster of clusters) {
    const columnEnds = [];
    const clusterPlaced = [];
    for (const event of cluster.events) {
      let column = columnEnds.findIndex((end) => end <= event._start);
      if (column === -1) {
        column = columnEnds.length;
        columnEnds.push(event._end);
      } else {
        columnEnds[column] = event._end;
      }
      clusterPlaced.push({ ...event, column });
    }
    const columns = Math.max(columnEnds.length, 1);
    for (const event of clusterPlaced) placed.push({ ...event, columns });
  }
  return placed;
}

export function renderTimetable(container, options) {
  const {
    sessions = [],
    events = [],
    term,
  } = options;

  const dayStart = toMinutes(term.day_start);
  const dayEnd = toMinutes(term.day_end);
  const totalMinutes = dayEnd - dayStart;
  const bodyHeight = `height: calc(${totalMinutes} * var(--minute-height))`;

  const head = DAYS.map((day) => {
    const daySessions = sessions.filter((session) => session.day === day);
    const dayEvents = events.filter((event) => event.day === day);
    const total = daySessions.reduce((sum, session) => sum + session.duration_minutes, 0);
    let label = daySessions.length
      ? `${daySessions.length} clase${daySessions.length === 1 ? "" : "s"} · ${formatDuration(total)}`
      : "libre";
    if (dayEvents.length) {
      label += ` · +${dayEvents.length} extra`;
    }
    return `
      <div class="tt-day-head" data-day="${day}">
        <span class="tt-day-name">${escapeHtml(DAY_LABELS[day] ?? day)}</span>
        <span class="tt-day-meta">${escapeHtml(label)}</span>
      </div>`;
  }).join("");

  const timeLabels = [];
  for (let minute = dayStart; minute <= dayEnd; minute += 60) {
    timeLabels.push(`
      <span class="tt-time" style="top: calc(${minute - dayStart} * var(--minute-height))">
        ${String(Math.floor(minute / 60)).padStart(2, "0")}:00
      </span>`);
  }

  const eventItems = events.map((event) => ({
    ...event,
    _isEvent: true,
    subject_name: event.title,
    group_code: "",
    room_name: event.location || "Extraescolar",
    building_name: "",
    group_type_label: "Extraescolar",
    color: event.color || "#7c5cbf",
    duration_minutes: toMinutes(event.end) - toMinutes(event.start),
    day_label: DAY_LABELS[event.day] ?? event.day,
    has_conflict: false,
  }));

  const columns = DAYS.map((day) => {
    const dayItems = layoutDay([
      ...sessions.filter((session) => session.day === day),
      ...eventItems.filter((event) => event.day === day),
    ]);
    const blocks = dayItems.map((session) => {
      const offset = toMinutes(session.start) - dayStart;
      const duration = session.duration_minutes;
      const gap = session.columns > 1 ? 1.4 : 1;
      const width = 100 / session.columns - gap;
      const left = session.column * (100 / session.columns) + gap / 2;
      const isEvent = Boolean(session._isEvent);
      const classes = [
        "tt-block",
        duration < 45 ? "is-short" : "",
        isEvent ? "is-event" : "",
        !isEvent && session.has_conflict ? "is-conflict" : "",
        !isEvent && session.pinned ? "is-pinned" : "",
        session.has_event_conflict ? "is-event-conflict" : "",
      ]
        .filter(Boolean)
        .join(" ");
      const tooltip = isEvent
        ? [
            `${session.title} ${session.location || "Extraescolar"}.`,
            `${session.day_label} ${session.start}–${session.end}.`,
            session.weeks
              ? `Actividad extraescolar de ${session.weeks.length} semana${session.weeks.length === 1 ? "" : "s"}.`
              : "Actividad extraescolar semanal.",
            session.has_event_conflict ? "¡Solapa con una clase!" : "",
          ]
            .filter(Boolean)
            .join(" ")
        : [
            `${session.subject_name} ${session.group_code} · ${session.room_name}.`,
            `${session.day_label} ${session.start}–${session.end}.`,
            `${session.group_type_label}, ${session.building_name}.`,
            session.source_session_ids?.length > 1
              ? `Bloque unido de ${session.source_session_ids.length} sesiones.`
              : "",
            session.has_conflict ? "¡Solapa con otra clase!" : "",
            session.has_event_conflict ? "¡Solapa con una extraescolar!" : "",
            session.pinned
              ? "Grupo fijado: «Resolver solapes» no lo cambiará. Clic para desfijar."
              : "Clic para fijar el grupo.",
          ]
            .filter(Boolean)
            .join(" ");
      const meta = isEvent
        ? session.location || "Extraescolar"
        : [session.group_code, session.group_type_label].filter(Boolean).join(" · ");
      const colorVar = isEvent
        ? `--event-color:${escapeHtml(session.color)}`
        : `--subject-color:${escapeHtml(session.color || "var(--accent)")}`;
      return `
        <button
          class="${classes}"
          type="button"
          ${isEvent ? `data-event-id="${escapeHtml(session.id)}"` : `data-session-id="${escapeHtml(session.id)}" data-subject-id="${escapeHtml(session.subject_id)}" data-group-id="${escapeHtml(session.group_id)}"`}
          style="--offset:${offset};--duration:${duration};--left:${left.toFixed(3)}%;--width:${width.toFixed(3)}%;${colorVar}"
          title="${escapeHtml(tooltip)}"
          aria-label="${escapeHtml(tooltip)}"
        >
          <span class="tt-block-name">${escapeHtml(isEvent ? session.title : session.subject_name)}</span>
          <span class="tt-block-meta">${escapeHtml(meta)}</span>
          ${isEvent ? "" : `<span class="tt-block-foot"><span class="tt-block-room">${escapeHtml(session.room_name || "")}</span><span class="tt-block-building">${escapeHtml(session.building_short_name || session.building_name || "")} 📍</span></span>`}
        </button>`;
    }).join("");

    return `
      <div class="tt-day" data-day="${day}" style="${bodyHeight}">
        ${blocks}
      </div>`;
  }).join("");

  const empty = sessions.length || events.length
    ? ""
    : `<div class="tt-empty"><div class="tt-empty-inner">
         <span class="tt-empty-icon">＋</span>
         <strong>Tu semana está en blanco</strong>
         <p>Elige un grupo en el panel de la izquierda.</p>
       </div></div>`;

  container.innerHTML = `
    <div class="tt">
      <div class="tt-head">
        <div class="tt-head-gutter"></div>
        ${head}
      </div>
      <div class="tt-body">
        <div class="tt-gutter" style="${bodyHeight}">${timeLabels.join("")}</div>
        ${columns}
        ${empty}
      </div>
    </div>`;
}

export { DAYS, formatDuration, layoutDay, toMinutes };
