/**
 * Client-side iCalendar (.ics, RFC 5545) generator.
 * Replaces backend/ics.py completely in static hosting environments like GitHub Pages.
 */

import { DAY_ORDER } from "./constants.js";
import { inWeek, weekInCurrentYear } from "./weeks.js";

const ICS_DAY = { mon: "MO", tue: "TU", wed: "WE", thu: "TH", fri: "FR", sat: "SA", sun: "SU" };
const WEEKDAY_OFFSET = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };

export function icsEscape(value) {
  if (!value) return "";
  return String(value)
    .replace(/\\/g, "\\\\")
    .replace(/;/g, "\\;")
    .replace(/,/g, "\\,")
    .replace(/\r\n|\n/g, "\\n");
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatIcsDate(d) {
  return `${d.getUTCFullYear()}${pad2(d.getUTCMonth() + 1)}${pad2(d.getUTCDate())}`;
}

function formatUtcTimestamp(date) {
  return `${date.getUTCFullYear()}${pad2(date.getUTCMonth() + 1)}${pad2(date.getUTCDate())}T${pad2(date.getUTCHours())}${pad2(date.getUTCMinutes())}${pad2(date.getUTCSeconds())}Z`;
}

function addDays(baseDate, days) {
  return new Date(baseDate.getTime() + days * 24 * 60 * 60 * 1000);
}

function vevent(uid, stamp, timezone, dayDate, start, end, weeklyOn, count, fields = {}) {
  const dayStr = formatIcsDate(dayDate);
  const startStr = start.replace(":", "");
  const endStr = end.replace(":", "");

  const lines = [
    "BEGIN:VEVENT",
    `UID:${uid}`,
    `DTSTAMP:${stamp}`,
    `DTSTART;TZID=${timezone}:${dayStr}T${startStr}00`,
    `DTEND;TZID=${timezone}:${dayStr}T${endStr}00`,
  ];

  if (weeklyOn && count) {
    lines.push(`RRULE:FREQ=WEEKLY;COUNT=${count};BYDAY=${ICS_DAY[weeklyOn] || "MO"}`);
  }

  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined && value !== null) {
      lines.push(`${key}:${value}`);
    }
  }

  lines.push("END:VEVENT");
  return lines;
}

export function buildCalendarIcs({ blocks = [], customEvents = [], term, name = "Horario USC" }) {
  const tz = term?.timezone || "Europe/Madrid";
  const termWeeks = term?.weeks || 15;
  const now = new Date();
  const stamp = formatUtcTimestamp(now);

  // Calculate next monday from today (UTC)
  const todayUtc = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const currentDayOfWeek = todayUtc.getUTCDay() || 7; // 1=Mon .. 7=Sun
  const daysUntilNextMonday = ((7 - currentDayOfWeek) % 7) || 7;
  const nextMonday = addDays(todayUtc, daysUntilNextMonday);

  const lines = [
    "BEGIN:VCALENDAR",
    "VERSION:2.0",
    "PRODID:-//Horario USC//Constructor de horarios//ES",
    "CALSCALE:GREGORIAN",
    "METHOD:PUBLISH",
    `X-WR-CALNAME:${icsEscape(name)}`,
    `X-WR-TIMEZONE:${tz}`,
  ];

  // Process class blocks
  for (const block of blocks) {
    const dayOffset = WEEKDAY_OFFSET[block.day] ?? 0;
    const location = [block.building_name, block.room_name].filter(Boolean).join(" · ");
    let description = `${block.group_type_label || "Clase"} · grupo ${block.group_code || ""} · ${block.subject_name || ""}. `;
    if ((block.source_session_ids?.length || 0) > 1) {
      description += `Bloque unido de ${block.source_session_ids.length} sesiones. `;
    }
    description += "Generado con el constructor de horarios.";

    if (block.weeks && block.weeks.length > 0) {
      // One event per dated week
      for (const weekId of block.weeks) {
        const monday = weekInCurrentYear(weekId);
        const eventDay = addDays(monday, dayOffset);
        const dateTag = formatIcsDate(eventDay);
        lines.push(
          ...vevent(
            `${block.id}-${dateTag}@horario.usc`,
            stamp,
            tz,
            eventDay,
            block.start,
            block.end,
            null,
            null,
            {
              SUMMARY: icsEscape(`${block.subject_name} (${block.group_code})`),
              LOCATION: icsEscape(location),
              DESCRIPTION: icsEscape(description),
            },
          ),
        );
      }
    } else {
      // Weekly repeating event
      const eventDay = addDays(nextMonday, dayOffset);
      lines.push(
        ...vevent(
          `${block.id}@horario.usc`,
          stamp,
          tz,
          eventDay,
          block.start,
          block.end,
          block.day,
          termWeeks,
          {
            SUMMARY: icsEscape(`${block.subject_name} (${block.group_code})`),
            LOCATION: icsEscape(location),
            DESCRIPTION: icsEscape(description),
          },
        ),
      );
    }
  }

  // Process custom extracurricular activities
  for (const event of customEvents) {
    const dayOffset = WEEKDAY_OFFSET[event.day] ?? 0;
    const location = event.place ? event.place : "";
    const description = "Actividad extraescolar añadida en el constructor de horarios.";

    if (event.weeks && event.weeks.length > 0) {
      for (const weekId of event.weeks) {
        const monday = weekInCurrentYear(weekId);
        const eventDay = addDays(monday, dayOffset);
        const dateTag = formatIcsDate(eventDay);
        lines.push(
          ...vevent(
            `evt-${event.id}-${dateTag}@horario.usc`,
            stamp,
            tz,
            eventDay,
            event.start,
            event.end,
            null,
            null,
            {
              SUMMARY: icsEscape(event.title),
              LOCATION: icsEscape(location),
              DESCRIPTION: icsEscape(description),
            },
          ),
        );
      }
    } else {
      const eventDay = addDays(nextMonday, dayOffset);
      lines.push(
        ...vevent(
          `evt-${event.id}@horario.usc`,
          stamp,
          tz,
          eventDay,
          event.start,
          event.end,
          event.day,
          termWeeks,
          {
            SUMMARY: icsEscape(event.title),
            LOCATION: icsEscape(location),
            DESCRIPTION: icsEscape(description),
          },
        ),
      );
    }
  }

  lines.push("END:VCALENDAR");
  return lines.join("\r\n");
}
