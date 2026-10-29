/**
 * ISO weeks, the device date and the week selector's patterns.
 *
 * Mirrors backend/timeutil.py: the catalogue may describe an earlier
 * academic year, so each week keeps its ISO number in this year's course.
 */

import { DAY_MS, MONTHS } from "./constants.js";
import { selectedGroupIds } from "./selection.js";
import { state } from "./state.js";
import { plural } from "./utils.js";

// ``weeks`` null means the meeting happens every week.
export function sharesWeek(first, second) {
  return !first || !second || first.some((week) => second.includes(week));
}

export function inWeek(session, weekId) {
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

export function weekInCurrentYear(weekId) {
  const calendar = state.term?.calendar ?? [];
  const first = calendar[0]?.id ?? weekId;
  const dataStart = academicYearStart(isoMonday(Number(first.slice(0, 4)), Number(first.slice(6))));
  const year = Number(weekId.slice(0, 4)) + academicYearStart(todayUtc()) - dataStart;
  return isoMonday(year, Math.min(Number(weekId.slice(6)), isoWeeksInYear(year)));
}

export function formatWeekDate(weekId) {
  const monday = weekInCurrentYear(weekId);
  return `${monday.getUTCDate()} ${MONTHS[monday.getUTCMonth()]}`;
}

// The catalogue week that matches today, or null outside the course.
export function currentWeekId() {
  const today = todayUtc();
  const monday = today.getTime() - ((today.getUTCDay() || 7) - 1) * DAY_MS;
  return (
    (state.term?.calendar ?? []).find((week) => weekInCurrentYear(week.id).getTime() === monday)?.id ??
    null
  );
}

// The term of the device date.  With weekly data the second term starts on
// the Monday of its first teaching week (moved to this academic year), so
// January exams still count as the first term; summer belongs to the
// second until August, when the next course begins.
export function semesterForToday() {
  const today = todayUtc();
  const firstOfSecond = (state.term?.calendar ?? []).find((week) => week.semester === 2);
  if (firstOfSecond) {
    const academicStart = Date.UTC(academicYearStart(today), 7, 1);
    return today >= weekInCurrentYear(firstOfSecond.id) && today >= academicStart ? 2 : 1;
  }
  const month = today.getUTCMonth();
  return month >= 1 && month <= 6 ? 2 : 1;
}

// Weeks of the term on screen that differ for the chosen groups.  Identical
// weeks share one entry; the most frequent one is "Horario habitual".
export function weekPatterns() {
  const weeks = (state.term?.calendar ?? []).filter((week) => week.semester === state.semester);
  if (!weeks.length) return [];
  const sessions = selectedGroupIds().flatMap((groupId) => state.groupIndex.get(groupId)?.sessions ?? []);
  const byKey = new Map();
  for (const week of weeks) {
    const ids = sessions.filter((session) => inWeek(session, week.id)).map((session) => session.id);
    // Weeks in which none of the chosen classes is taught are not offered.
    if (sessions.length && !ids.length) continue;
    const eventIds = state.customEvents.filter((event) => inWeek(event, week.id)).map((event) => event.id);
    const key = `${ids.sort().join(",")}|${eventIds.sort().join(",")}`;
    if (!byKey.has(key)) byKey.set(key, { weeks: [], sessionIds: new Set(ids), eventIds: new Set(eventIds) });
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
  const addedEvents = [...pattern.eventIds].filter((id) => !usual.eventIds.has(id)).length;
  const removedEvents = [...usual.eventIds].filter((id) => !pattern.eventIds.has(id)).length;
  const changes = [];
  if (added) changes.push(`${plural(added, "clase", "clases")} más`);
  if (removed) changes.push(`${plural(removed, "clase", "clases")} menos`);
  if (addedEvents) changes.push(`${plural(addedEvents, "extraescolar", "extraescolares")} más`);
  if (removedEvents) changes.push(`${plural(removedEvents, "extraescolar", "extraescolares")} menos`);
  if (!changes.length) changes.push("cambio de aula");
  return `${title} · ${changes.join(", ")}`;
}

// The week whose classes are on screen, or null when the catalogue has a
// single typical week.  Keeps ``state.weekId`` pointing at a valid week.
export function activeWeekId() {
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
