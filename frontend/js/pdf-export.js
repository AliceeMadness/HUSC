/**
 * The week on screen as a one-page A4 landscape PDF, drawn as vectors with
 * the same layout as the on-screen timetable (overlaps side by side).
 */

import { A4_LANDSCAPE, PdfDocument, fitText, hexToRgb, mix, textWidth, wrapText } from "./pdf.js";
import { DAYS, formatDuration, layoutDay, toMinutes } from "./timetable.js";

const DAY_NAMES = { mon: "Lunes", tue: "Martes", wed: "Miércoles", thu: "Jueves", fri: "Viernes" };
const MARGIN = 28;
const GUTTER = 32;
const HEAD = 30;
const FOOTER = 16;

const WHITE = [1, 1, 1];
const INK = hexToRgb("#1d2433");
const MUTED = hexToRgb("#5d6677");
const LINE = hexToRgb("#d8dde6");
const FAINT = hexToRgb("#eef1f5");
const ACCENT = hexToRgb("#000066");
const DANGER = hexToRgb("#c62828");

// Hours shown: the classes' span, rounded to whole hours, inside the term.
function hourRange(items, term) {
  const first = toMinutes(term.day_start);
  const last = toMinutes(term.day_end);
  if (!items.length) return [first, last];
  const start = Math.min(...items.map((item) => toMinutes(item.start)));
  const end = Math.max(...items.map((item) => toMinutes(item.end)));
  return [Math.max(first, Math.floor(start / 60) * 60), Math.min(last, Math.ceil(end / 60) * 60)];
}

function drawGrid(pdf, { left, top, width, height, dayWidth, from, to, perMinute, sessions, events }) {
  pdf.rect(left, top, width, height + HEAD, { stroke: LINE, lineWidth: 0.8 });
  pdf.line(left, top + HEAD, left + width, top + HEAD, { stroke: LINE, lineWidth: 0.8 });
  DAYS.forEach((day, index) => {
    const x = left + GUTTER + index * dayWidth;
    pdf.line(x, top, x, top + HEAD + height, { stroke: LINE, lineWidth: 0.8 });
    const daySessions = sessions.filter((session) => session.day === day);
    const dayEvents = events.filter((event) => event.day === day);
    const total = daySessions.reduce((sum, session) => sum + session.duration_minutes, 0);
    let meta = daySessions.length
      ? `${daySessions.length} clase${daySessions.length === 1 ? "" : "s"} · ${formatDuration(total).replace("′", " min")}`
      : "libre";
    if (dayEvents.length) meta += ` · +${dayEvents.length} extra`;
    pdf.text(DAY_NAMES[day], x + 6, top + 13, { size: 10, font: "bold", color: INK });
    pdf.text(fitText(meta, dayWidth - 12, 7, "regular"), x + 6, top + 23, { size: 7, color: MUTED });
  });
  for (let minute = from; minute <= to; minute += 30) {
    const y = top + HEAD + (minute - from) * perMinute;
    const hour = minute % 60 === 0;
    if (minute > from && minute < to) {
      pdf.line(left + GUTTER, y, left + width, y, hour ? { stroke: LINE, lineWidth: 0.5 } : { stroke: FAINT, lineWidth: 0.5, dash: [2, 2] });
    }
    if (hour && minute < to) {
      const label = `${String(minute / 60).padStart(2, "0")}:00`;
      pdf.text(label, left + GUTTER - 5, y + 8, { size: 7, color: MUTED, align: "right" });
    }
  }
}

// One class or activity: tinted box, coloured edge and as much text as fits.
function drawBlock(pdf, item, { x, y, width, height }) {
  const color = hexToRgb(item.color || "#004c93");
  const conflict = item.has_conflict || item.has_event_conflict;
  pdf.roundedRect(x, y, width, height, 2.5, {
    fill: mix(color, WHITE, 0.16),
    stroke: conflict ? DANGER : null,
    lineWidth: 1,
    dash: item._isEvent ? [3, 2] : null,
  });
  pdf.rect(x, y, 2.4, height, { fill: color });

  const inner = width - 8;
  const textColor = mix(color, INK, 0.55);
  const lineHeight = 8.4;
  let lines = Math.floor((height - 4) / lineHeight);
  if (lines < 1) return;
  const meta = item._isEvent
    ? item.location || "Extraescolar"
    : [item.group_code, item.group_type_label].filter(Boolean).join(" · ");
  const foot = item._isEvent ? null : [item.room_name || "", item.building_short_name || item.building_name || ""];
  const reserved = Math.min(lines - 1, foot ? 2 : 1);
  const name = wrapText(item._isEvent ? item.title : item.subject_name, inner, 7.4, "bold", Math.max(1, Math.min(3, lines - reserved)));
  let baseline = y + 9;
  for (const line of name) {
    pdf.text(line, x + 5, baseline, { size: 7.4, font: "bold", color: INK });
    baseline += lineHeight;
  }
  lines -= name.length;
  if (lines > 0 && meta) {
    pdf.text(fitText(meta, inner, 6.6, "regular"), x + 5, baseline, { size: 6.6, color: textColor });
    lines -= 1;
  }
  if (lines > 0 && foot) {
    const [room, building] = foot;
    const footY = Math.max(baseline + lineHeight, y + height - 3.5);
    const buildingText = fitText(building, inner * 0.45, 6.6, "bold");
    const roomWidth = inner - textWidth(buildingText, 6.6, "bold") - 4;
    pdf.text(buildingText, x + width - 4, footY, { size: 6.6, font: "bold", color: textColor, align: "right" });
    pdf.text(fitText(room, roomWidth, 6.6, "regular"), x + 5, footY, { size: 6.6, color: textColor });
  }
}

/**
 * ``sessions`` are the (merged) classes of the week on screen and ``events``
 * its activities, as given to ``renderTimetable``.  Returns a PDF Blob.
 */
export function timetablePdf({ title, subtitle, sessions, events, term }) {
  const pdf = new PdfDocument(A4_LANDSCAPE, { title }).addPage();
  const { width: pageWidth, height: pageHeight } = A4_LANDSCAPE;

  pdf.text(title, MARGIN, MARGIN + 14, { size: 18, font: "bold", color: ACCENT });
  pdf.text(fitText(subtitle, pageWidth - 2 * MARGIN - 120, 9.5, "regular"), MARGIN, MARGIN + 29, { size: 9.5, color: MUTED });
  pdf.text("HUSC", pageWidth - MARGIN, MARGIN + 14, { size: 12, font: "bold", color: ACCENT, align: "right" });

  const items = [
    ...sessions,
    ...events.map((event) => ({
      ...event,
      _isEvent: true,
      duration_minutes: toMinutes(event.end) - toMinutes(event.start),
    })),
  ];
  const [from, to] = hourRange(items, term);
  const top = MARGIN + 42;
  const left = MARGIN;
  const width = pageWidth - 2 * MARGIN;
  const height = pageHeight - MARGIN - FOOTER - top - HEAD;
  const dayWidth = (width - GUTTER) / DAYS.length;
  const perMinute = height / (to - from);
  drawGrid(pdf, { left, top, width, height, dayWidth, from, to, perMinute, sessions, events });

  DAYS.forEach((day, index) => {
    const columnLeft = left + GUTTER + index * dayWidth;
    for (const item of layoutDay(items.filter((entry) => entry.day === day))) {
      const start = Math.max(item._start, from);
      const end = Math.min(item._end, to);
      if (end <= start) continue;
      const slot = (dayWidth - 4) / item.columns;
      drawBlock(pdf, item, {
        x: columnLeft + 2 + item.column * slot + 0.8,
        y: top + HEAD + (start - from) * perMinute + 0.8,
        width: slot - 1.6,
        height: (end - start) * perMinute - 1.6,
      });
    }
  });

  if (!items.length) {
    pdf.text("Tu semana está en blanco", left + width / 2, top + HEAD + height / 2, { size: 12, color: MUTED, align: "center" });
  }
  const conflicts = items.some((item) => item.has_conflict || item.has_event_conflict);
  const note = conflicts ? "Los recuadros en rojo se solapan con otra clase o actividad." : "";
  const date = new Date().toLocaleDateString("es-ES", { day: "numeric", month: "long", year: "numeric" });
  pdf.text(note, MARGIN, pageHeight - MARGIN + 4, { size: 7.5, color: DANGER });
  pdf.text(`Generado el ${date}`, pageWidth - MARGIN, pageHeight - MARGIN + 4, { size: 7.5, color: MUTED, align: "right" });
  return pdf.toBlob();
}
