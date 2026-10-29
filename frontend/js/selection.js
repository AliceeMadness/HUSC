/**
 * The plan in memory: indexes over its subjects and groups, and the groups
 * the student has chosen (``state.selected``) and pinned.
 */

import { GROUP_TYPE_ORDER, PALETTE } from "./constants.js";
import { state } from "./state.js";

// Index the subjects, groups and sessions of ``/api/plan`` and give each
// subject its colour.
export function buildIndexes(data) {
  state.lastResolve = null;
  state.subjects = [];
  state.groupIndex = new Map();
  state.sessionById = new Map();

  data.subjects.forEach((subject, subjectIndex) => {
    const color = PALETTE[subjectIndex % PALETTE.length];
    const groups = subject.groups.map((group) => {
      const sessions = group.sessions.map((session) => {
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
        return enriched;
      });
      const enrichedGroup = {
        ...group,
        subject_id: subject.id,
        subject_name: subject.name,
        subject_index: subjectIndex,
        color,
        sessions,
      };
      state.groupIndex.set(group.id, enrichedGroup);
      return enrichedGroup;
    });
    state.subjects.push({ ...subject, color, groups });
  });
}

export function setGroupSelection(groupId) {
  const group = state.groupIndex.get(groupId);
  if (!group) return;
  const bySubject = (state.selected[group.subject_id] = state.selected[group.subject_id] || {});
  const ids = (bySubject[group.type] = bySubject[group.type] || []);
  if (!ids.includes(group.id)) ids.push(group.id);
}

// Several groups of the same type can be chosen at once (a lecturer may
// teach CLE01 and CLE02), so a click only toggles that one group.
export function toggleGroupSelection(subjectId, type, groupId) {
  const current = selectedOfType(subjectId, type);
  if (!current.includes(groupId)) {
    setGroupSelection(groupId);
    return;
  }
  state.selected[subjectId][type] = current.filter((id) => id !== groupId);
  if (!state.selected[subjectId][type].length) delete state.selected[subjectId][type];
  if (!Object.keys(state.selected[subjectId]).length) delete state.selected[subjectId];
}

// Chosen groups of one subject and type, in the order they were picked.
export function selectedOfType(subjectId, type) {
  return state.selected[subjectId]?.[type] ?? [];
}

export function subjectsOfSemester(semester) {
  return state.subjects.filter((subject) => (subject.semester ?? 1) === semester);
}

// Subjects of the term on screen.
export function semesterSubjects() {
  return subjectsOfSemester(state.semester);
}

export function semesterSubjectIds() {
  return semesterSubjects().map((subject) => subject.id);
}

// Groups of the term on screen; everything the timetable, validation and
// overlap repair look at.
export function selectedGroupIds() {
  return groupIdsFor(semesterSubjects());
}

// Groups of both terms; what is saved and shared.
export function allSelectedGroupIds() {
  return groupIdsFor(state.subjects);
}

export function groupIdsFor(subjects) {
  const ids = [];
  for (const subject of subjects) {
    const selection = state.selected[subject.id];
    if (!selection) continue;
    const types = Object.keys(selection).sort(
      (a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9),
    );
    for (const type of types) ids.push(...selection[type]);
  }
  return ids;
}

export function selectedCount() {
  return selectedGroupIds().length;
}

export function selectedSubjectCount() {
  return semesterSubjects().filter((subject) => Object.keys(state.selected[subject.id] || {}).length)
    .length;
}

// Group types of the visible term with nothing chosen yet.
export function missingGroups() {
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

export function isPinnedGroup(groupId) {
  return state.pinnedGroupIds.has(groupId);
}

// Pins only make sense on chosen groups of the plan in memory.
export function prunePins() {
  const selected = new Set(allSelectedGroupIds());
  state.pinnedGroupIds = new Set(
    [...state.pinnedGroupIds].filter((groupId) => selected.has(groupId) && state.groupIndex.has(groupId)),
  );
}
