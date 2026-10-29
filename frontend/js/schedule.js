/**
 * Instant, client-side timetable: merged blocks, overlaps and the next class.
 *
 * The overlap rule is the twin of ``_overlaps``/``find_conflicts`` in
 * backend/scheduling: same day, intervals that cut ``[start, end)``, same
 * term and at least one common ISO week.  Change both together.
 */

import { DAY_LABELS, DAY_ORDER, UPCOMING_MINUTES, WEEKDAY_CODES } from "./constants.js";
import { groupIdsFor, isPinnedGroup, selectedGroupIds, subjectsOfSemester } from "./selection.js";
import { state } from "./state.js";
import { toMinutes } from "./timetable.js";
import { minutesToHm } from "./utils.js";
import { activeWeekId, currentWeekId, inWeek, sharesWeek } from "./weeks.js";

const byDayAndStart = (a, b) =>
  (DAY_ORDER[a.day] ?? 9) - (DAY_ORDER[b.day] ?? 9) || toMinutes(a.start) - toMinutes(b.start);

// Overlap of two intervals on the same day, in minutes, or null.
function overlap(a, b) {
  const start = Math.max(toMinutes(a.start), toMinutes(b.start));
  const end = Math.min(toMinutes(a.end), toMinutes(b.end));
  return start < end ? { start, end } : null;
}

function startBlock(session) {
  return {
    ...session,
    id: `blk-${session.id}`,
    source_session_ids: [session.id],
    has_conflict: false,
    has_event_conflict: false,
    conflict_with: [],
  };
}

// Consecutive classes of the same group and room become one block
// (9–10 + 10–11 → 9–11), keeping the original session ids.
export function mergeLocalSessions(sessions) {
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
      if (current && current.end === session.start) {
        current.end = session.end;
        current.duration_minutes += session.duration_minutes;
        current.source_session_ids.push(session.id);
        current.pinned = current.pinned || session.pinned;
        continue;
      }
      if (current) merged.push(current);
      current = startBlock(session);
    }
    if (current) merged.push(current);
  }
  return merged.sort(
    (a, b) => byDayAndStart(a, b) || a.subject_name.localeCompare(b.subject_name, "es"),
  );
}

// The blocks of the week on screen, their overlaps and their clashes with
// extracurricular activities.
export function buildLocalSchedule() {
  const weekId = activeWeekId();
  const rawSessions = [];
  for (const groupId of selectedGroupIds()) {
    const group = state.groupIndex.get(groupId);
    if (!group) continue;
    for (const session of group.sessions) {
      if (!inWeek(session, weekId)) continue;
      rawSessions.push({ ...session, pinned: isPinnedGroup(groupId), has_conflict: false, conflict_with: [] });
    }
  }
  const sessions = mergeLocalSessions(rawSessions);

  const conflicts = [];
  for (let i = 0; i < sessions.length; i += 1) {
    for (let j = i + 1; j < sessions.length; j += 1) {
      const a = sessions[i];
      const b = sessions[j];
      if (a.day !== b.day) continue;
      const cut = overlap(a, b);
      if (!cut) continue;
      a.has_conflict = true;
      b.has_conflict = true;
      a.conflict_with.push(b.id);
      b.conflict_with.push(a.id);
      conflicts.push({
        day: a.day,
        day_label: DAY_LABELS[a.day],
        start: minutesToHm(cut.start),
        end: minutesToHm(cut.end),
        overlap_minutes: cut.end - cut.start,
        left: a,
        right: b,
      });
    }
  }
  conflicts.sort(byDayAndStart);

  const eventConflicts = [];
  for (const event of state.customEvents.filter((item) => inWeek(item, weekId))) {
    for (const session of sessions) {
      if (session.day !== event.day) continue;
      const cut = overlap(session, event);
      if (!cut) continue;
      session.has_event_conflict = true;
      eventConflicts.push({
        event,
        session,
        day: event.day,
        day_label: DAY_LABELS[event.day],
        start: minutesToHm(cut.start),
        end: minutesToHm(cut.end),
        overlap_minutes: cut.end - cut.start,
      });
    }
  }
  eventConflicts.sort(byDayAndStart);

  return { sessions, conflicts, eventConflicts };
}

function sessionsOverlap(a, b) {
  if (a.day !== b.day || !sharesWeek(a.weeks, b.weeks)) return false;
  return toMinutes(a.start) < toMinutes(b.end) && toMinutes(b.start) < toMinutes(a.end);
}

// Whether picking ``group`` would clash with the groups already chosen.
export function groupConflicts(group) {
  for (const selectedId of selectedGroupIds()) {
    const selectedGroup = state.groupIndex.get(selectedId);
    // Picking a group no longer replaces its siblings, so only the group
    // itself is skipped: CLE01 and CLE02 at the same hour do clash.
    if (!selectedGroup || selectedGroup.id === group.id) continue;
    for (const candidate of group.sessions) {
      for (const selected of selectedGroup.sessions) {
        if (sessionsOverlap(candidate, selected)) return true;
      }
    }
  }
  return false;
}

// Classes of the chosen groups that start within the next hour, by the
// device clock: today's real week and term, whatever is on screen.
export function upcomingClasses() {
  const weekId = currentWeekId();
  if (!weekId) return null;
  const semester = (state.term?.calendar ?? []).find((week) => week.id === weekId)?.semester;
  const now = new Date();
  const day = WEEKDAY_CODES[now.getDay()];
  const nowMinutes = now.getHours() * 60 + now.getMinutes();
  const sessions = groupIdsFor(subjectsOfSemester(semester))
    .flatMap((groupId) => state.groupIndex.get(groupId)?.sessions ?? [])
    .filter((session) => session.day === day && inWeek(session, weekId));
  const soon = mergeLocalSessions(sessions).filter((block) => {
    const wait = toMinutes(block.start) - nowMinutes;
    return wait > 0 && wait <= UPCOMING_MINUTES;
  });
  if (!soon.length) return null;
  const first = Math.min(...soon.map((block) => toMinutes(block.start)));
  return {
    minutes: first - nowMinutes,
    blocks: soon.filter((block) => toMinutes(block.start) === first),
  };
}
