/**
 * Client-side schedule repair ("Resolver solapes").
 * Replaces backend/scheduling/repair.py in static hosting environments like GitHub Pages.
 */

import { GROUP_TYPE_ORDER } from "./constants.js";
import { getGroupById, getSessionsByGroupId, getSubjectById } from "./client-store.js";
import { sharesWeek } from "./weeks.js";

const GROUP_TYPE_LABELS = {
  expositive: "Expositiva",
  laboratory: "Laboratorio",
  seminar: "Seminario",
  other: "Otro",
};

function toMinutes(hm) {
  const [h, m] = hm.split(":").map(Number);
  return h * 60 + m;
}

function naturalCompare(a, b) {
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

function sessionOverlapsEvent(session, event) {
  if (session.day !== event.day) return false;
  if (!sharesWeek(session.weeks, event.weeks)) return false;
  const sStart = toMinutes(session.start);
  const sEnd = toMinutes(session.end);
  const eStart = toMinutes(event.start);
  const eEnd = toMinutes(event.end);
  return sStart < eEnd && eStart < sEnd;
}

function sessionsOverlap(a, b) {
  if (a.day !== b.day) return false;
  if (a.semester && b.semester && a.semester !== b.semester) return false;
  if (a.group_id && b.group_id && a.group_id === b.group_id) return false;
  if (!sharesWeek(a.weeks, b.weeks)) return false;
  const aStart = toMinutes(a.start);
  const aEnd = toMinutes(a.end);
  const bStart = toMinutes(b.start);
  const bEnd = toMinutes(b.end);
  return aStart < bEnd && bStart < aEnd;
}

function quality(groups, events = []) {
  const sessions = groups.flatMap((group) => getSessionsByGroupId(group.id));
  let conflictPairs = 0;
  let conflictMinutes = 0;
  for (let i = 0; i < sessions.length; i += 1) {
    for (let j = i + 1; j < sessions.length; j += 1) {
      const a = sessions[i];
      const b = sessions[j];
      if (a.group_id !== b.group_id && sessionsOverlap(a, b)) {
        conflictPairs += 1;
        const aStart = toMinutes(a.start);
        const aEnd = toMinutes(a.end);
        const bStart = toMinutes(b.start);
        const bEnd = toMinutes(b.end);
        conflictMinutes += Math.min(aEnd, bEnd) - Math.max(aStart, bStart);
      }
    }
  }

  let eventHits = 0;
  if (events.length > 0) {
    for (const session of sessions) {
      if (events.some((e) => sessionOverlapsEvent(session, e))) {
        eventHits += 1;
      }
    }
  }

  const byDay = new Map();
  for (const session of sessions) {
    if (!byDay.has(session.day)) byDay.set(session.day, []);
    byDay.get(session.day).push([toMinutes(session.start), toMinutes(session.end)]);
  }

  let gaps = 0;
  let latestEnd = 0;
  let earliestStart = 24 * 60;
  for (const intervals of byDay.values()) {
    intervals.sort((a, b) => a[0] - b[0]);
    const merged = [];
    for (const [start, end] of intervals) {
      if (!merged.length || start > merged[merged.length - 1][1]) {
        merged.push([start, end]);
      } else {
        merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], end);
      }
    }
    const span = merged[merged.length - 1][1] - merged[0][0];
    const occupied = merged.reduce((sum, [s, e]) => sum + (e - s), 0);
    gaps += Math.max(0, span - occupied);
    latestEnd = Math.max(latestEnd, merged[merged.length - 1][1]);
    earliestStart = Math.min(earliestStart, merged[0][0]);
  }

  return [
    conflictPairs,
    eventHits,
    byDay.size,
    gaps,
    conflictMinutes,
    latestEnd,
    earliestStart < 24 * 60 ? earliestStart : 0,
  ];
}

function compareQuality(a, b) {
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function addCost(a, b) {
  return [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
}

function compareCost(a, b) {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] - b[i];
  }
  return 0;
}

function buildClashIndex(variables, events) {
  const allGroups = [...new Map(variables.flatMap((v) => v.groups).map((g) => [g.id, g])).values()];
  const blockedIds = new Set();
  for (const group of allGroups) {
    const sessions = getSessionsByGroupId(group.id);
    if (events.some((evt) => sessions.some((s) => sessionOverlapsEvent(s, evt)))) {
      blockedIds.add(group.id);
    }
  }

  const clashes = new Map(allGroups.map((g) => [g.id, new Set()]));
  for (let i = 0; i < allGroups.length; i += 1) {
    const first = allGroups[i];
    const firstSessions = getSessionsByGroupId(first.id);
    for (let j = i + 1; j < allGroups.length; j += 1) {
      const second = allGroups[j];
      const secondSessions = getSessionsByGroupId(second.id);
      let clash = false;
      for (const a of firstSessions) {
        for (const b of secondSessions) {
          if (sessionsOverlap(a, b)) {
            clash = true;
            break;
          }
        }
        if (clash) break;
      }
      if (clash) {
        clashes.get(first.id).add(second.id);
        clashes.get(second.id).add(first.id);
      }
    }
  }

  return { blockedIds, clashes };
}

export function repairSolution({
  variables,
  current,
  pinnedIndices,
  events = [],
  maxSolutions = 60,
  nodeLimit = 150000,
  timeLimit = 3.0,
}) {
  if (!variables.length) {
    return { best: [], meta: { nodes: 0, solutions_found: 0, exhausted: true } };
  }

  const { blockedIds, clashes } = buildClashIndex(variables, events);

  function canPlace(group, usedSet) {
    if (blockedIds.has(group.id) || usedSet.has(group.id)) return false;
    const groupClashes = clashes.get(group.id);
    for (const u of usedSet) {
      if (groupClashes.has(u)) return false;
    }
    return true;
  }

  function optionCost(index, group) {
    const chosen = current[index];
    if (!group) return chosen ? [1, 0, 0] : [0, 0, 1];
    if (chosen && chosen !== group.id) return [0, 1, 0];
    return [0, 0, 0];
  }

  function lowerBound(remaining, usedSet) {
    let bound = [0, 0, 0];
    for (const index of remaining) {
      const chosen = current[index];
      const placeable = variables[index].groups.filter((g) => canPlace(g, usedSet));
      if (!placeable.length) {
        bound = addCost(bound, optionCost(index, null));
      } else if (chosen && !placeable.some((g) => g.id === chosen)) {
        bound = addCost(bound, [0, 1, 0]);
      }
    }
    return bound;
  }

  const order = variables.map((_, idx) => idx).sort((a, b) => variables[a].groups.length - variables[b].groups.length);

  const candidates = new Map();
  for (let index = 0; index < variables.length; index += 1) {
    const variable = variables[index];
    const chosen = current[index];
    const ranked = [...variable.groups].sort((a, b) => {
      const aNotChosen = a.id !== chosen ? 1 : 0;
      const bNotChosen = b.id !== chosen ? 1 : 0;
      if (aNotChosen !== bNotChosen) return aNotChosen - bNotChosen;
      const q = compareQuality(quality([a], events), quality([b], events));
      if (q !== 0) return q;
      return naturalCompare(a.code, b.code);
    });
    candidates.set(index, pinnedIndices.has(index) ? ranked : [...ranked, null]);
  }

  const assignment = new Map();
  const used = new Set();
  let bestCost = null;
  let solutions = [];
  let nodes = 0;
  let exhausted = true;
  const deadline = performance.now() + timeLimit * 1000;

  function dfs(position, cost) {
    if (nodes >= nodeLimit || (nodes % 256 === 0 && performance.now() > deadline)) {
      exhausted = false;
      return;
    }
    if (position === order.length) {
      if (bestCost === null || compareCost(cost, bestCost) < 0) {
        bestCost = cost;
        solutions = [];
      }
      if (solutions.length < maxSolutions) {
        solutions.push(variables.map((_, idx) => assignment.get(idx)));
      }
      return;
    }

    const variableIndex = order[position];
    for (const group of candidates.get(variableIndex)) {
      nodes += 1;
      if (nodes >= nodeLimit) {
        exhausted = false;
        return;
      }
      if (group && !canPlace(group, used)) continue;

      const nextCost = addCost(cost, optionCost(variableIndex, group));
      if (bestCost !== null && compareCost(nextCost, bestCost) > 0) continue;

      assignment.set(variableIndex, group);
      if (group) used.add(group.id);

      const bound = addCost(nextCost, lowerBound(order.slice(position + 1), used));
      if (
        bestCost === null ||
        compareCost(bound, bestCost) < 0 ||
        (compareCost(bound, bestCost) === 0 && solutions.length < maxSolutions)
      ) {
        dfs(position + 1, nextCost);
      }

      if (group) used.delete(group.id);
      assignment.delete(variableIndex);

      if (!exhausted) return;
    }
  }

  dfs(0, [0, 0, 0]);

  const meta = {
    nodes,
    solutions_found: solutions.length,
    node_limit: nodeLimit,
    max_solutions: maxSolutions,
    time_limit: timeLimit,
    exhausted,
  };

  if (!solutions.length || bestCost === null) {
    return { best: null, meta };
  }

  let best = solutions[0];
  let bestQ = quality(best.filter(Boolean), events);
  for (let i = 1; i < solutions.length; i += 1) {
    const q = quality(solutions[i].filter(Boolean), events);
    if (compareQuality(q, bestQ) < 0) {
      best = solutions[i];
      bestQ = q;
    }
  }

  const [removed, changed, unfilled] = bestCost;
  Object.assign(meta, { removed, changed, unfilled });
  return { best, meta };
}

export function optimizeSchedule({
  groupIds = [],
  subjectIds = [],
  customEvents = [],
  pinnedGroupIds = [],
  maxSolutions = 60,
  nodeLimit = 150000,
}) {
  const requested = [...new Set(subjectIds)];
  if (!requested.length) throw new Error("La matrícula no puede estar vacía");

  const groupsNow = groupIds.map(getGroupById).filter(Boolean);
  const pinnedSet = new Set(pinnedGroupIds);

  // One variable per chosen group, matching Python _repair_variables
  const variables = [];
  const current = [];
  const pinnedIndices = new Set();

  for (const subjectId of requested) {
    const subject = getSubjectById(subjectId);
    if (!subject) continue;
    const allSubjectGroups = (subject.group_ids || []).map(getGroupById).filter(Boolean);
    const byType = new Map();
    for (const g of allSubjectGroups) {
      if (!byType.has(g.type)) byType.set(g.type, []);
      byType.get(g.type).push(g);
    }

    const sortedTypes = [...byType.keys()].sort((a, b) => (GROUP_TYPE_ORDER[a] ?? 9) - (GROUP_TYPE_ORDER[b] ?? 9));
    for (const type of sortedTypes) {
      const candidates = byType.get(type).sort((a, b) => naturalCompare(a.code, b.code));
      const chosenInType = groupsNow.filter((g) => g.subject_id === subjectId && g.type === type);
      for (const group of chosenInType) {
        if (pinnedSet.has(group.id)) {
          pinnedIndices.add(variables.length);
          variables.push({ subjectId, type, groups: [group] });
        } else {
          variables.push({ subjectId, type, groups: candidates });
        }
        current.push(group.id);
      }
    }
  }

  const { best: repaired, meta } = repairSolution({
    variables,
    current,
    pinnedIndices,
    events: customEvents,
    maxSolutions,
    nodeLimit,
  });

  let status = "optimized";
  let finalIds = [];
  if (!repaired) {
    status = "impossible";
    finalIds = groupsNow.map((g) => g.id);
  } else {
    finalIds = repaired.filter(Boolean).map((g) => g.id);
    if (meta.removed) {
      status = "partial";
    } else if (new Set(finalIds).size === current.filter(Boolean).length && finalIds.every((id) => current.includes(id))) {
      status = "unchanged";
    } else {
      status = "optimized";
    }
  }

  const changes = [];
  for (let i = 0; i < variables.length; i += 1) {
    const before = current[i];
    const after = repaired ? (repaired[i]?.id ?? null) : before;
    if (before === after) continue;
    const variable = variables[i];
    const sub = getSubjectById(variable.subjectId);
    const fromGroup = before ? getGroupById(before) : null;
    const toGroup = after ? getGroupById(after) : null;
    changes.push({
      subject_id: variable.subjectId,
      subject_name: sub?.name || variable.subjectId,
      type: variable.type,
      type_label: GROUP_TYPE_LABELS[variable.type] || variable.type,
      from_group_id: before,
      from_group_code: fromGroup?.code || null,
      to_group_id: after,
      to_group_code: toGroup?.code || null,
    });
  }

  return {
    status,
    solver: { status, ...meta },
    pinned_group_ids: [...pinnedSet],
    requested_subject_ids: requested,
    group_ids: finalIds,
    changes,
  };
}
