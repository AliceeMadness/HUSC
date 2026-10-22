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
    term,
    candidateSlots = [],
    activeCandidate = null,
    movingSessionId = null,
  } = options;

  const dayStart = toMinutes(term.day_start);
  const dayEnd = toMinutes(term.day_end);
  const totalMinutes = dayEnd - dayStart;
  const bodyHeight = `height: calc(${totalMinutes} * var(--minute-height))`;

  const head = DAYS.map((day) => {
    const daySessions = sessions.filter((session) => session.day === day);
    const total = daySessions.reduce((sum, session) => sum + session.duration_minutes, 0);
    const label = daySessions.length
      ? `${daySessions.length} clase${daySessions.length === 1 ? "" : "s"} · ${formatDuration(total)}`
      : "libre";
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

  const columns = DAYS.map((day) => {
    const daySessions = layoutDay(sessions.filter((session) => session.day === day));
    const blocks = daySessions.map((session) => {
      const offset = toMinutes(session.start) - dayStart;
      const duration = session.duration_minutes;
      const gap = session.columns > 1 ? 1.4 : 1;
      const width = 100 / session.columns - gap;
      const left = session.column * (100 / session.columns) + gap / 2;
      const classes = [
        "tt-block",
        duration < 45 ? "is-short" : "",
        session.has_conflict ? "is-conflict" : "",
        session.has_pref_conflict ? "is-pref" : "",
        session.id === movingSessionId ? "is-moving" : "",
      ]
        .filter(Boolean)
        .join(" ");
      // Start with the exact visible label so assistive technologies (and
      // Lighthouse's label-content-name-mismatch rule) get a matching name.
      const tooltip = [
        `${session.subject_name} ${session.group_code} · ${session.room_name}.`,
        `${session.day_label} ${session.start}–${session.end}.`,
        `${session.group_type_label}, ${session.building_name}.`,
        session.has_conflict ? "¡Solapa con otra clase!" : "",
        session.has_pref_conflict ? "Cae en una franja que querías evitar." : "",
      ]
        .filter(Boolean)
        .join(" ");
      const meta = `${session.group_code} · ${session.room_name}`;
      return `
        <button
          class="${classes}"
          type="button"
          data-session-id="${escapeHtml(session.id)}"
          data-subject-id="${escapeHtml(session.subject_id)}"
          data-group-id="${escapeHtml(session.group_id)}"
          style="--offset:${offset};--duration:${duration};--left:${left.toFixed(3)}%;--width:${width.toFixed(3)}%;--subject-color:${escapeHtml(session.color || "var(--accent)")}"
          title="${escapeHtml(tooltip)}"
          aria-label="${escapeHtml(tooltip)}"
        >
          <span class="tt-block-name">${escapeHtml(session.subject_name)}</span>
          <span class="tt-block-meta">${escapeHtml(meta)}</span>
        </button>`;
    }).join("");

    const candidates = candidateSlots
      .filter((slot) => slot.day === day)
      .map((slot) => {
        const offset = toMinutes(slot.start) - dayStart;
        const duration = toMinutes(slot.end) - toMinutes(slot.start);
        const active =
          activeCandidate &&
          activeCandidate.day === slot.day &&
          activeCandidate.start === slot.start
            ? "is-active"
            : "";
        return `
          <span
            class="tt-candidate ${active}"
            aria-hidden="true"
            style="--offset:${offset};--duration:${duration}"
          >libre</span>`;
      })
      .join("");

    return `
      <div class="tt-day" data-day="${day}" style="${bodyHeight}">
        ${blocks}
        ${candidates}
      </div>`;
  }).join("");

  const empty = sessions.length
    ? ""
    : `<div class="tt-empty"><div class="tt-empty-inner">
         <span class="tt-empty-icon">＋</span>
         <strong>Tu semana está en blanco</strong>
         <p>Elige un grupo en la izquierda o deja que el piloto automático haga la primera propuesta.</p>
       </div></div>`;

  container.innerHTML = `
    <div class="tt ${movingSessionId ? "is-moving" : ""}">
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

export { DAYS, formatDuration, toMinutes };
