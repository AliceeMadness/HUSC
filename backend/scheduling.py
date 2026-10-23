"""Conflict detection, free-slot search and the timetable generator.

All algorithms operate on the canonical catalogue and return plain JSON-ready
dictionaries.  The generator is a small constraint solver: one variable per
(subject, group type), one value per group, and the constraint that no two
weekly sessions overlap.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .ids import natural_key
from .models import GROUP_TYPE_LABELS, GROUP_TYPE_ORDER, ClassSession, Group, GroupType
from .store import CatalogStore
from .timeutil import DAY_LABELS, format_hm, parse_hm

DAYS = ("mon", "tue", "wed", "thu", "fri")
DAY_ORDER = {day: index for index, day in enumerate(DAYS)}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _minutes(value: str) -> int:
    return parse_hm(value)


def _day_index(day: str) -> int:
    return DAY_ORDER.get(day, len(DAYS))


def normalize_events(events: list[dict] | None) -> list[dict]:
    result = []
    for event in events or []:
        day = str(event.get("day", "")).lower()
        start = str(event.get("start", ""))
        end = str(event.get("end", ""))
        if day not in DAY_ORDER:
            raise ValueError(f"día de extraescolar no válido: {day!r}")
        start_min, end_min = _minutes(start), _minutes(end)
        if end_min <= start_min:
            raise ValueError(f"horario de extraescolar inválido: {start}-{end}")
        result.append(
            {
                "id": event.get("id"),
                "title": event.get("title") or "Extraescolar",
                "day": day,
                "start": start,
                "end": end,
                "start_min": start_min,
                "end_min": end_min,
                "duration_minutes": end_min - start_min,
                "location": event.get("location"),
                "color": event.get("color"),
                "kind": event.get("kind", "extracurricular"),
                "note": event.get("note"),
            }
        )
    return result


def normalize_blocked(blocked: list[dict] | None) -> list[dict]:
    result: list[dict] = []
    for item in blocked or []:
        day = str(item.get("day", "")).lower()
        start = str(item.get("start", ""))
        end = str(item.get("end", ""))
        if day not in DAY_ORDER:
            raise ValueError(f"día bloqueado no válido: {day!r}")
        start_min, end_min = _minutes(start), _minutes(end)
        if end_min <= start_min:
            raise ValueError(f"franja bloqueada inválida: {start}-{end}")
        result.append(
            {"day": day, "start": start, "end": end, "start_min": start_min, "end_min": end_min}
        )
    return result


def resolve_groups(store: CatalogStore, group_ids: list[str]) -> list[Group]:
    ordered = list(dict.fromkeys(group_ids))
    unknown = [group_id for group_id in ordered if group_id not in store.group_by_id]
    if unknown:
        raise ValueError(f"Grupos desconocidos: {', '.join(unknown)}")
    return [store.group_by_id[group_id] for group_id in ordered]


def sessions_for_groups(
    store: CatalogStore,
    groups: list[Group],
    ignore_session_ids: set[str] | None = None,
) -> list[ClassSession]:
    ignored = ignore_session_ids or set()
    sessions = []
    for group in groups:
        for session in store.sessions_by_group[group.id]:
            if session.id not in ignored:
                sessions.append(session)
    sessions.sort(
        key=lambda session: (
            _day_index(session.day.value),
            _minutes(session.start),
            store.subject_by_id[session.subject_id].name,
            store.group_by_id[session.group_id].code,
        )
    )
    return sessions


def _overlaps(a: ClassSession, b: ClassSession) -> bool:
    return (
        a.day == b.day
        and a.semester == b.semester
        and _minutes(a.start) < _minutes(b.end)
        and _minutes(b.start) < _minutes(a.end)
    )


def _overlaps_blocked(session: ClassSession, blocked: list[dict]) -> bool:
    start, end = _minutes(session.start), _minutes(session.end)
    return any(
        item["day"] == session.day.value and start < item["end_min"] and item["start_min"] < end
        for item in blocked
    )


def find_conflicts(views: list[dict]) -> list[dict]:
    """Every overlap is reported once as an unordered pair."""

    conflicts: list[dict] = []
    for index, first in enumerate(views):
        for second in views[index + 1 :]:
            if first["day"] != second["day"]:
                continue
            if first.get("semester", 1) != second.get("semester", 1):
                continue
            # A group meeting in two rooms at once is one class, not a clash.
            if first.get("group_id") and first.get("group_id") == second.get("group_id"):
                continue
            first_start, first_end = _minutes(first["start"]), _minutes(first["end"])
            second_start, second_end = _minutes(second["start"]), _minutes(second["end"])
            if first_start < second_end and second_start < first_end:
                conflicts.append(
                    {
                        "day": first["day"],
                        "day_label": DAY_LABELS[first["day"]],
                        "start": format_hm(max(first_start, second_start)),
                        "end": format_hm(min(first_end, second_end)),
                        "overlap_minutes": min(first_end, second_end) - max(first_start, second_start),
                        "left": {
                            "id": first["id"],
                            "subject_name": first["subject_name"],
                            "group_code": first["group_code"],
                            "start": first["start"],
                            "end": first["end"],
                        },
                        "right": {
                            "id": second["id"],
                            "subject_name": second["subject_name"],
                            "group_code": second["group_code"],
                            "start": second["start"],
                            "end": second["end"],
                        },
                    }
                )
    conflicts.sort(
        key=lambda conflict: (
            _day_index(conflict["day"]),
            _minutes(conflict["start"]),
            conflict["left"]["subject_name"],
        )
    )
    return conflicts


def free_intervals(
    views: list[dict],
    blocked: list[dict] | None = None,
    day_start: str = "08:00",
    day_end: str = "21:00",
    min_duration: int = 15,
) -> list[dict]:
    """Complement of occupied (and blocked) time, per weekday."""

    if min_duration < 0:
        raise ValueError("min_duration no puede ser negativo")
    window_start, window_end = _minutes(day_start), _minutes(day_end)
    if window_end <= window_start:
        raise ValueError("day_end debe ser posterior a day_start")

    occupied: dict[str, list[tuple[int, int]]] = {day: [] for day in DAYS}
    for view in views:
        if view["day"] not in occupied:
            continue
        occupied[view["day"]].append((_minutes(view["start"]), _minutes(view["end"])))
    for item in blocked or []:
        if item["day"] not in occupied:
            continue
        occupied[item["day"]].append((item["start_min"], item["end_min"]))

    result: list[dict] = []
    for day in DAYS:
        intervals = sorted(occupied[day])
        merged: list[list[int]] = []
        for start, end in intervals:
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)

        cursor = window_start
        gaps: list[tuple[int, int]] = []
        for start, end in merged:
            if end <= window_start or start >= window_end:
                continue
            if start > cursor:
                gaps.append((cursor, min(start, window_end)))
            cursor = max(cursor, min(end, window_end))
        if cursor < window_end:
            gaps.append((cursor, window_end))

        for start, end in gaps:
            duration = end - start
            if duration >= min_duration:
                result.append(
                    {
                        "day": day,
                        "day_label": DAY_LABELS[day],
                        "start": format_hm(start),
                        "end": format_hm(end),
                        "duration_minutes": duration,
                    }
                )
    return result


# ---------------------------------------------------------------------------
# Validation / enriched selection
# ---------------------------------------------------------------------------


def _enriched_sessions(
    store: CatalogStore,
    sessions: list[ClassSession],
) -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    views = [store.session_view(session) for session in sessions]
    conflicts = find_conflicts(views)
    conflict_map: dict[str, list[str]] = {}
    for conflict in conflicts:
        left, right = conflict["left"]["id"], conflict["right"]["id"]
        conflict_map.setdefault(left, []).append(right)
        conflict_map.setdefault(right, []).append(left)
    for view in views:
        view["has_conflict"] = view["id"] in conflict_map
        view["conflict_with"] = conflict_map.get(view["id"], [])
    return views, conflicts, conflict_map


def merge_session_views(views: list[dict]) -> list[dict]:
    """Merge contiguous raw sessions into one visual block.

    A 09:00-10:00 + 10:00-11:00 pair becomes a single 09:00-11:00 block.  The
    raw session ids are kept in ``source_session_ids`` so moving or exporting a
    block can still address the underlying records.
    """

    groups: dict[tuple, list[dict]] = {}
    for view in views:
        key = (
            view["subject_id"],
            view["group_id"],
            view["group_type"],
            view["day"],
            view["room_id"],
            view["building_id"],
        )
        groups.setdefault(key, []).append(view)

    blocks: list[dict] = []
    for group_views in groups.values():
        group_views.sort(key=lambda item: _minutes(item["start"]))
        current: dict | None = None
        for view in group_views:
            if current is None:
                current = {
                    **view,
                    "id": f"blk-{view['id']}",
                    "session_ids": [view["id"]],
                    "conflict_with": list(view.get("conflict_with", [])),
                    "has_conflict": bool(view.get("has_conflict")),
                }
                continue
            if _minutes(current["end"]) == _minutes(view["start"]):
                current["end"] = view["end"]
                current["duration_minutes"] += view["duration_minutes"]
                current["session_ids"].append(view["id"])
                current["has_conflict"] = current["has_conflict"] or bool(
                    view.get("has_conflict")
                )
                current["conflict_with"] = list(
                    dict.fromkeys(current["conflict_with"] + list(view.get("conflict_with", [])))
                )
            else:
                blocks.append(current)
                current = {
                    **view,
                    "id": f"blk-{view['id']}",
                    "session_ids": [view["id"]],
                    "conflict_with": list(view.get("conflict_with", [])),
                    "has_conflict": bool(view.get("has_conflict")),
                }
        if current is not None:
            blocks.append(current)

    blocks.sort(
        key=lambda block: (
            _day_index(block["day"]),
            _minutes(block["start"]),
            block["subject_name"],
            block["group_code"],
        )
    )
    return blocks


def _missing_requirements(
    store: CatalogStore,
    groups: list[Group],
    subject_ids: list[str] | None = None,
) -> list[dict]:
    if subject_ids is not None:
        # An explicit plan (matrícula) is the source of truth, even when the
        # student has not selected a group yet.  This is what makes repeated
        # courses and double degrees work.
        check_ids = list(dict.fromkeys(subject_ids))
        unknown = [
            subject_id for subject_id in check_ids if subject_id not in store.subject_by_id
        ]
        if unknown:
            raise ValueError(f"Asignaturas desconocidas: {', '.join(unknown)}")
    else:
        if not groups:
            return []
        selected_subject_ids = list(dict.fromkeys(group.subject_id for group in groups))
        years = {
            store.subject_by_id[subject_id].year_id for subject_id in selected_subject_ids
        }
        # Backwards-compatible inference: a single-year selection means "all
        # subjects of that year"; a mixed selection only checks what was chosen.
        if len(years) == 1:
            year_id = next(iter(years))
            check_ids = list(store.year_by_id[year_id].subject_ids)
        else:
            check_ids = selected_subject_ids
    missing = []
    for subject_id in check_ids:
        subject = store.subject_by_id[subject_id]
        all_groups = [store.group_by_id[group_id] for group_id in subject.group_ids]
        expected_types = {group.type for group in all_groups}
        selected_types = {group.type for group in groups if group.subject_id == subject_id}
        for group_type in sorted(expected_types - selected_types, key=lambda item: GROUP_TYPE_ORDER[item]):
            missing.append(
                {
                    "subject_id": subject.id,
                    "subject_name": subject.name,
                    "type": group_type.value,
                    "type_label": GROUP_TYPE_LABELS[group_type],
                    "available_group_ids": [
                        group.id for group in all_groups if group.type == group_type
                    ],
                }
            )
    return missing


def _selection_view(store: CatalogStore, groups: list[Group]) -> list[dict]:
    subject_ids = list(dict.fromkeys(group.subject_id for group in groups))
    result = []
    for subject_id in subject_ids:
        subject = store.subject_by_id[subject_id]
        selected = [group for group in groups if group.subject_id == subject_id]
        selected.sort(key=lambda group: (GROUP_TYPE_ORDER[group.type], natural_key(group.code)))
        result.append(
            {
                "subject_id": subject.id,
                "subject_name": subject.name,
                "groups": [
                    {
                        "group_id": group.id,
                        "code": group.code,
                        "type": group.type.value,
                        "type_label": group.type_label,
                    }
                    for group in selected
                ],
            }
        )
    return result


def _event_conflicts(views: list[dict], events: list[dict]) -> list[dict]:
    result = []
    for event in events:
        for view in views:
            if view["day"] != event["day"]:
                continue
            start, end = _minutes(view["start"]), _minutes(view["end"])
            if start >= event["end_min"] or event["start_min"] >= end:
                continue
            result.append(
                {
                    "event": event,
                    "session": view,
                    "day": event["day"],
                    "day_label": DAY_LABELS[event["day"]],
                    "start": format_hm(max(start, event["start_min"])),
                    "end": format_hm(min(end, event["end_min"])),
                    "overlap_minutes": min(end, event["end_min"])
                    - max(start, event["start_min"]),
                }
            )
    result.sort(key=lambda item: (_day_index(item["day"]), _minutes(item["start"])))
    return result


def _blocked_conflicts(views: list[dict], blocked: list[dict]) -> list[dict]:
    result = []
    for view in views:
        start, end = _minutes(view["start"]), _minutes(view["end"])
        for slot in blocked:
            if view["day"] != slot["day"] or start >= slot["end_min"] or slot["start_min"] >= end:
                continue
            result.append(
                {
                    "session": view,
                    "blocked": {
                        "day": slot["day"],
                        "day_label": DAY_LABELS[slot["day"]],
                        "start": slot["start"],
                        "end": slot["end"],
                    },
                    "overlap_minutes": min(end, slot["end_min"]) - max(start, slot["start_min"]),
                }
            )
            break
    return result


def _stats(views: list[dict], conflicts: list[dict], blocked_conflicts: list[dict]) -> dict:
    active_days = sorted({view["day"] for view in views}, key=_day_index)
    return {
        "total_sessions": len(views),
        "weekly_minutes": sum(view["duration_minutes"] for view in views),
        "active_days": len(active_days),
        "active_day_codes": active_days,
        "conflict_count": len(conflicts),
        "conflict_minutes": sum(conflict["overlap_minutes"] for conflict in conflicts),
        "blocked_conflict_count": len(blocked_conflicts),
        "first_class": min((view["start"] for view in views), default=None),
        "last_class": max((view["end"] for view in views), default=None),
    }


def validate_selection(
    store: CatalogStore,
    group_ids: list[str],
    blocked: list[dict] | None = None,
    min_free_minutes: int = 30,
    day_start: str = "08:00",
    day_end: str = "21:00",
    subject_ids: list[str] | None = None,
    custom_events: list[dict] | None = None,
) -> dict:
    groups = resolve_groups(store, group_ids)
    blocked_norm = normalize_blocked(blocked)
    events_norm = normalize_events(custom_events)
    if subject_ids is not None:
        plan_set = set(subject_ids)
        outside_plan = [group.id for group in groups if group.subject_id not in plan_set]
        if outside_plan:
            raise ValueError(
                "Hay grupos de asignaturas que no están en la matrícula: "
                + ", ".join(outside_plan)
            )
    sessions = sessions_for_groups(store, groups)
    views, conflicts, _ = _enriched_sessions(store, sessions)
    blocks = merge_session_views(views)
    blocked_conflicts = _blocked_conflicts(views, blocked_norm)
    event_conflicts = _event_conflicts(views, events_norm)
    free = free_intervals(
        views + [event for event in events_norm if event["day"] in DAY_ORDER],
        blocked=blocked_norm,
        day_start=day_start,
        day_end=day_end,
        min_duration=min_free_minutes,
    )
    stats = _stats(views, conflicts, blocked_conflicts)
    stats["total_blocks"] = len(blocks)
    stats["event_conflict_count"] = len(event_conflicts)
    return {
        "ok": len(conflicts) == 0 and len(blocked_conflicts) == 0 and len(event_conflicts) == 0,
        "group_ids": [group.id for group in groups],
        "subject_ids": list(dict.fromkeys(subject_ids)) if subject_ids is not None else None,
        "selections": _selection_view(store, groups),
        "missing": _missing_requirements(store, groups, subject_ids=subject_ids),
        "sessions": views,
        "blocks": blocks,
        "conflicts": conflicts,
        "blocked_conflicts": blocked_conflicts,
        "event_conflicts": event_conflicts,
        "custom_events": events_norm,
        "free_slots": free,
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


@dataclass
class Variable:
    subject_id: str
    type: GroupType
    groups: list[Group]

    @property
    def key(self) -> tuple[str, GroupType]:
        return (self.subject_id, self.type)


def _quality(store: CatalogStore, groups: list[Group], blocked: list[dict]) -> tuple:
    """Lower is better: conflicts, blocked hits, days, idle gaps, latest end."""

    sessions = [session for group in groups for session in store.sessions_by_group[group.id]]
    conflict_pairs = 0
    conflict_minutes = 0
    for index, first in enumerate(sessions):
        for second in sessions[index + 1 :]:
            if first.group_id != second.group_id and _overlaps(first, second):
                conflict_pairs += 1
                first_start, first_end = _minutes(first.start), _minutes(first.end)
                second_start, second_end = _minutes(second.start), _minutes(second.end)
                conflict_minutes += min(first_end, second_end) - max(first_start, second_start)
    blocked_hits = sum(1 for session in sessions if _overlaps_blocked(session, blocked))

    by_day: dict[str, list[tuple[int, int]]] = {}
    for session in sessions:
        by_day.setdefault(session.day.value, []).append(
            (_minutes(session.start), _minutes(session.end))
        )
    gaps = 0
    latest_end = 0
    earliest_start = 24 * 60
    for intervals in by_day.values():
        intervals.sort()
        merged: list[list[int]] = []
        for start, end in intervals:
            if not merged or start > merged[-1][1]:
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        span = merged[-1][1] - merged[0][0]
        occupied = sum(end - start for start, end in merged)
        gaps += max(0, span - occupied)
        latest_end = max(latest_end, merged[-1][1])
        earliest_start = min(earliest_start, merged[0][0])

    return (
        conflict_pairs,
        blocked_hits,
        len(by_day),
        gaps,
        conflict_minutes,
        latest_end,
        earliest_start if earliest_start < 24 * 60 else 0,
    )


def _build_variables(store: CatalogStore, subject_ids: list[str]) -> list[Variable]:
    """One variable per (subject, group type), for any mix of years/degrees."""

    requested = list(dict.fromkeys(subject_ids))
    if not requested:
        raise ValueError("La matrícula no puede estar vacía")
    unknown = [subject_id for subject_id in requested if subject_id not in store.subject_by_id]
    if unknown:
        raise ValueError(f"Asignaturas desconocidas: {', '.join(unknown)}")

    variables: list[Variable] = []
    for subject_id in requested:
        subject = store.subject_by_id[subject_id]
        groups = [store.group_by_id[group_id] for group_id in subject.group_ids]
        by_type: dict[GroupType, list[Group]] = {}
        for group in groups:
            by_type.setdefault(group.type, []).append(group)
        for group_type in sorted(by_type, key=lambda item: GROUP_TYPE_ORDER[item]):
            candidates = sorted(by_type[group_type], key=lambda group: natural_key(group.code))
            variables.append(Variable(subject_id, group_type, candidates))
    return variables


def _apply_pins(
    store: CatalogStore,
    variables: list[Variable],
    pinned_group_ids: list[str] | None,
) -> list[Variable]:
    """Narrow each pinned variable to its fixed group.

    Pins are group-level because the generator chooses groups, not individual
    meetings.  A pinned group keeps *all* its weekly sessions immovable.
    """

    if not pinned_group_ids:
        return variables
    pinned_by_key: dict[tuple[str, GroupType], Group] = {}
    for group_id in dict.fromkeys(pinned_group_ids):
        group = store.group_by_id.get(group_id)
        if group is None:
            raise ValueError(f"Grupo fijado desconocido: {group_id}")
        key = (group.subject_id, group.type)
        previous = pinned_by_key.get(key)
        if previous is not None and previous.id != group.id:
            raise ValueError(
                f"No se pueden fijar dos grupos del mismo tipo: {previous.code} y {group.code}"
            )
        pinned_by_key[key] = group

    present_keys = {variable.key for variable in variables}
    missing = [key for key in pinned_by_key if key not in present_keys]
    if missing:
        labels = [
            f"{store.subject_by_id[subject_id].name} ({group_type.value})"
            for subject_id, group_type in missing
        ]
        raise ValueError("Hay grupos fijados fuera de la matrícula: " + ", ".join(labels))

    result = []
    for variable in variables:
        pinned = pinned_by_key.get(variable.key)
        if pinned is None:
            result.append(variable)
            continue
        if pinned.id not in {group.id for group in variable.groups}:
            raise ValueError(
                f"El grupo fijado {pinned.code} no pertenece a la asignatura "
                f"{store.subject_by_id[variable.subject_id].name}"
            )
        result.append(Variable(variable.subject_id, variable.type, [pinned]))
    return result


def _plan_years(store: CatalogStore, subject_ids: list[str]) -> list[dict]:
    year_ids = list(
        dict.fromkeys(store.subject_by_id[subject_id].year_id for subject_id in subject_ids)
    )
    return [store.year_summary(store.year_by_id[year_id]) for year_id in year_ids]


def _strict_solution(
    store: CatalogStore,
    variables: list[Variable],
    blocked: list[dict],
    max_solutions: int,
    node_limit: int,
) -> tuple[list[Group] | None, dict]:
    if not variables:
        return [], {"nodes": 0, "solutions_found": 0, "node_limit": node_limit, "exhausted": True}

    sessions_by_group = {group.id: store.sessions_by_group[group.id] for var in variables for group in var.groups}

    def can_place(group: Group, used: list[ClassSession]) -> bool:
        group_sessions = sessions_by_group[group.id]
        if any(_overlaps_blocked(session, blocked) for session in group_sessions):
            return False
        return all(
            not any(_overlaps(session, other) for other in used) for session in group_sessions
        )

    # Fail-first variable order, and compact candidates first.
    order = sorted(
        range(len(variables)),
        key=lambda index: (
            len(variables[index].groups),
            -sum(
                len(store.sessions_by_group[group.id]) for group in variables[index].groups[:1]
            ),
            index,
        ),
    )
    candidates: dict[int, list[Group]] = {}
    for index, variable in enumerate(variables):
        candidates[index] = sorted(
            variable.groups,
            key=lambda group: (_quality(store, [group], blocked), natural_key(group.code)),
        )

    assignment: dict[int, Group] = {}
    used: list[ClassSession] = []
    solutions: list[list[Group]] = []
    nodes = 0
    exhausted = True

    def dfs(position: int) -> None:
        nonlocal nodes, exhausted
        if len(solutions) >= max_solutions:
            exhausted = False
            return
        if nodes >= node_limit:
            exhausted = False
            return
        if position == len(order):
            solutions.append([assignment[index] for index in range(len(variables))])
            return

        variable_index = order[position]
        for group in candidates[variable_index]:
            nodes += 1
            if nodes >= node_limit:
                exhausted = False
                return
            if not can_place(group, used):
                continue
            group_sessions = sessions_by_group[group.id]
            assignment[variable_index] = group
            used.extend(group_sessions)

            supported = True
            for remaining in order[position + 1 :]:
                if not any(can_place(candidate, used) for candidate in candidates[remaining]):
                    supported = False
                    break
            if supported:
                dfs(position + 1)

            del used[len(used) - len(group_sessions) :]
            del assignment[variable_index]
            if len(solutions) >= max_solutions or nodes >= node_limit:
                return

    dfs(0)

    meta = {
        "nodes": nodes,
        "solutions_found": len(solutions),
        "node_limit": node_limit,
        "max_solutions": max_solutions,
        "exhausted": exhausted,
    }
    if not solutions:
        return None, meta
    best = min(solutions, key=lambda groups: _quality(store, groups, blocked))
    return best, meta


def _relaxed_solution(
    store: CatalogStore,
    variables: list[Variable],
    blocked: list[dict],
    passes: int = 12,
) -> tuple[list[Group], dict]:
    if not variables:
        return [], {"text": "sin variables"}

    assignment: dict[int, Group] = {}
    for index, variable in enumerate(variables):
        chosen = min(
            variable.groups,
            key=lambda group: (
                _quality(store, list(assignment.values()) + [group], blocked),
                natural_key(group.code),
            ),
        )
        assignment[index] = chosen

    def score(candidate: dict[int, Group]) -> tuple:
        return _quality(store, [candidate[index] for index in range(len(variables))], blocked)

    best_score = score(assignment)
    changes = 0
    for _ in range(passes):
        improved = False
        for index, variable in enumerate(variables):
            current = assignment[index]
            local_best, local_score = current, best_score
            for group in variable.groups:
                if group.id == current.id:
                    continue
                trial = dict(assignment)
                trial[index] = group
                trial_score = score(trial)
                if trial_score < local_score:
                    local_best, local_score = group, trial_score
            if local_best.id != current.id:
                assignment[index] = local_best
                best_score = local_score
                improved = True
                changes += 1
        if not improved:
            break

    return [assignment[index] for index in range(len(variables))], {
        "passes": passes,
        "changes": changes,
        "quality": best_score,
    }


def _subject_flexibility(store: CatalogStore, subject_id: str, original_index: int) -> tuple:
    subject = store.subject_by_id[subject_id]
    groups = [store.group_by_id[group_id] for group_id in subject.group_ids]
    by_type: dict[GroupType, int] = {}
    for group in groups:
        by_type[group.type] = by_type.get(group.type, 0) + 1
    return (
        min(by_type.values()) if by_type else 0,
        len(groups),
        original_index,
    )


def _greedy_subset(
    store: CatalogStore,
    subject_ids: list[str],
    order: list[str],
    blocked: list[dict],
    node_limit: int,
    pinned_group_ids: list[str] | None = None,
) -> tuple[list[str], list[Group], dict]:
    """Add subjects one by one, keeping only those that still fit.

    Fourth-year degrees contain electives that share time slots, and custom
    plans can mix very different years.  Rather than returning a schedule full
    of overlaps, we return the largest subset we can fit conflict-free and tell
    the UI which subjects were left out.
    """

    pinned_subjects = {
        store.group_by_id[group_id].subject_id
        for group_id in pinned_group_ids or []
        if group_id in store.group_by_id
    }
    included: list[str] = [subject_id for subject_id in subject_ids if subject_id in pinned_subjects]
    current: list[Group] = []
    if included:
        variables = _apply_pins(store, _build_variables(store, included), pinned_group_ids)
        groups, _ = _strict_solution(
            store,
            variables,
            blocked,
            max_solutions=1,
            node_limit=min(node_limit, 30_000),
        )
        if groups is None:
            return [], [], {"subset_attempts": len(order), "pinned_infeasible": True}
        current = groups

    for subject_id in order:
        if subject_id in pinned_subjects:
            continue
        trial = included + [subject_id]
        variables = _apply_pins(store, _build_variables(store, trial), pinned_group_ids)
        groups, meta = _strict_solution(
            store,
            variables,
            blocked,
            max_solutions=1,
            node_limit=min(node_limit, 30_000),
        )
        if groups is not None:
            included = trial
            current = groups

    if not included:
        return [], [], {"subset_attempts": len(order)}

    ordered_included = [subject_id for subject_id in subject_ids if subject_id in included]
    variables = _apply_pins(
        store, _build_variables(store, ordered_included), pinned_group_ids
    )
    groups, meta = _strict_solution(store, variables, blocked, max_solutions=60, node_limit=node_limit)
    return ordered_included, groups or current, {"subset_attempts": len(order), **meta}


def generate_schedule(
    store: CatalogStore,
    year_id: str | None = None,
    blocked: list[dict] | None = None,
    subject_ids: list[str] | None = None,
    allow_partial: bool = True,
    allow_conflicts: bool = True,
    max_solutions: int = 60,
    node_limit: int = 200_000,
    custom_events: list[dict] | None = None,
    pinned_group_ids: list[str] | None = None,
) -> dict:
    """Generate a timetable for a year (legacy) or an arbitrary plan.

    ``subject_ids`` is the new contract: it may mix subjects from several
    academic years and degrees.  ``year_id`` remains as a convenience for
    single-year plans and old API clients.  ``custom_events`` are treated as
    occupied time; ``pinned_group_ids`` are kept exactly as chosen.
    """

    blocked_norm = normalize_blocked(blocked)
    events_norm = normalize_events(custom_events)
    solver_blocked = blocked_norm + [
        {**event, "start_min": event["start_min"], "end_min": event["end_min"]}
        for event in events_norm
    ]
    pinned = list(dict.fromkeys(pinned_group_ids or []))
    year_data: dict | None = None

    if subject_ids is None:
        if year_id is None:
            raise ValueError("Indica year_id o subject_ids")
        year = store.year_by_id.get(year_id)
        if year is None:
            raise KeyError(year_id)
        requested = list(year.subject_ids)
        year_data = year.model_dump(mode="json")
    else:
        requested = list(dict.fromkeys(subject_ids))
        if year_id is not None:
            year = store.year_by_id.get(year_id)
            if year is None:
                raise KeyError(year_id)
            if set(requested) <= set(year.subject_ids):
                year_data = year.model_dump(mode="json")

    variables = _apply_pins(store, _build_variables(store, requested), pinned)
    best, meta = _strict_solution(store, variables, solver_blocked, max_solutions, node_limit)

    included = requested
    excluded: list[str] = []
    if best is not None:
        status = "optimal"
        groups = best
        solver = {"status": status, **meta}
    else:
        subset_result = None
        if allow_partial and len(requested) > 1:
            plan_order = {subject_id: index for index, subject_id in enumerate(requested)}
            orders = [
                sorted(
                    requested,
                    key=lambda subject_id: _subject_flexibility(
                        store, subject_id, plan_order.get(subject_id, 0)
                    ),
                ),
                requested,
                list(reversed(requested)),
            ]
            candidates = []
            for order in orders:
                candidate = _greedy_subset(
                    store,
                    requested,
                    order,
                    solver_blocked,
                    node_limit,
                    pinned_group_ids=pinned,
                )
                if candidate[0]:
                    candidates.append(candidate)
            if candidates:
                subset_result = max(
                    candidates,
                    key=lambda item: (
                        len(item[0]),
                        tuple(-value for value in _quality(store, item[1], blocked_norm)),
                        -len(
                            [
                                session
                                for group in item[1]
                                for session in store.sessions_by_group[group.id]
                            ]
                        ),
                    ),
                )
        if subset_result is not None:
            included, groups, subset_meta = subset_result
            excluded = [subject_id for subject_id in requested if subject_id not in included]
            status = "partial"
            solver = {"status": status, **meta, "subset": subset_meta}
            # Re-run validate with the subset; variables must match the subset too.
            variables = _apply_pins(store, _build_variables(store, included), pinned)
        elif allow_conflicts:
            groups, relaxed_meta = _relaxed_solution(store, variables, solver_blocked)
            status = "relaxed"
            solver = {"status": status, **meta, "relaxed": relaxed_meta}
        else:
            groups = []
            status = "impossible"
            solver = {"status": status, **meta}

    result = validate_selection(
        store,
        [group.id for group in groups],
        blocked=blocked_norm,
        day_start=store.catalog.term.day_start,
        day_end=store.catalog.term.day_end,
        subject_ids=included,
        custom_events=events_norm,
    )
    result["status"] = status
    result["solver"] = solver
    result["pinned_group_ids"] = pinned
    result["year"] = year_data
    result["years"] = _plan_years(store, included)
    degree_ids = list(dict.fromkeys(year["degree_id"] for year in result["years"]))
    result["degrees"] = [
        store.degree_by_id[degree_id].model_dump(mode="json") for degree_id in degree_ids
    ]
    result["requested_subject_ids"] = requested
    result["excluded_subjects"] = [
        {
            "id": subject_id,
            "name": store.subject_by_id[subject_id].name,
            "year_id": store.subject_by_id[subject_id].year_id,
            "year_name": store.year_by_id[store.subject_by_id[subject_id].year_id].name,
            "degree_short_name": store.degree_by_id[
                store.year_by_id[store.subject_by_id[subject_id].year_id].degree_id
            ].short_name,
        }
        for subject_id in excluded
    ]
    result["assignments"] = [
        {
            "subject_id": variable.subject_id,
            "subject_name": store.subject_by_id[variable.subject_id].name,
            "year_id": store.subject_by_id[variable.subject_id].year_id,
            "year_name": store.year_by_id[
                store.subject_by_id[variable.subject_id].year_id
            ].name,
            "degree_short_name": store.degree_by_id[
                store.year_by_id[store.subject_by_id[variable.subject_id].year_id].degree_id
            ].short_name,
            "type": variable.type.value,
            "type_label": GROUP_TYPE_LABELS[variable.type],
            "group_id": groups[index].id,
            "group_code": groups[index].code,
        }
        for index, variable in enumerate(variables)
        if index < len(groups)
    ] if groups else []
    return result


def _repair_solution(
    store: CatalogStore,
    variables: list[Variable],
    current: list[str | None],
    pinned_indices: set[int],
    hard_blocked: list[dict],
    soft_blocked: list[dict],
    max_solutions: int,
    node_limit: int,
    time_limit: float = 4.0,
) -> tuple[list[Group | None] | None, dict]:
    """Remove overlaps while touching as few of the user's groups as possible.

    ``current[i]`` is the group the user has in variable ``i`` (or ``None``).
    A subject may have several variables of the same type when the user
    chose several of its groups (a lecturer teaching CLE01 and CLE02); a
    group can then fill only one of them.

    Each variable may keep its current group, switch to another group or, as
    a last resort, be left empty.  Cost is lexicographic: groups removed,
    then groups changed, then unselected slots left empty.  Among the
    cheapest repairs, the one with fewest preference hits and best compactness
    wins.  Branch and bound with an admissible lower bound keeps it fast.
    """

    if not variables:
        return [], {"nodes": 0, "solutions_found": 0, "node_limit": node_limit, "exhausted": True}

    # Pairwise group clashes are precomputed so each search step is a set test.
    all_groups = list({group.id: group for var in variables for group in var.groups}.values())
    blocked_ids = {
        group.id
        for group in all_groups
        if any(_overlaps_blocked(session, hard_blocked) for session in store.sessions_by_group[group.id])
    }
    clashes: dict[str, set[str]] = {group.id: set() for group in all_groups}
    for index, first in enumerate(all_groups):
        for second in all_groups[index + 1 :]:
            if any(
                _overlaps(a, b)
                for a in store.sessions_by_group[first.id]
                for b in store.sessions_by_group[second.id]
            ):
                clashes[first.id].add(second.id)
                clashes[second.id].add(first.id)

    def can_place(group: Group, used: set[str]) -> bool:
        return (
            group.id not in blocked_ids
            and group.id not in used
            and not (clashes[group.id] & used)
        )

    def option_cost(index: int, group: Group | None) -> tuple[int, int, int]:
        chosen = current[index]
        if group is None:
            return (1, 0, 0) if chosen else (0, 0, 1)
        if chosen and chosen != group.id:
            return (0, 1, 0)
        return (0, 0, 0)

    def add(a: tuple[int, int, int], b: tuple[int, int, int]) -> tuple[int, int, int]:
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def lower_bound(remaining: list[int], used: set[str]) -> tuple[int, int, int]:
        bound = (0, 0, 0)
        for index in remaining:
            variable = variables[index]
            chosen = current[index]
            placeable = [group for group in variable.groups if can_place(group, used)]
            if not placeable:
                bound = add(bound, option_cost(index, None))
            elif chosen and chosen not in {group.id for group in placeable}:
                bound = add(bound, (0, 1, 0))
        return bound

    # Fail-first: variables with fewer alternatives are decided first.
    order = sorted(range(len(variables)), key=lambda index: (len(variables[index].groups), index))
    candidates: dict[int, list[Group | None]] = {}
    for index, variable in enumerate(variables):
        chosen = current[index]
        ranked: list[Group | None] = sorted(
            variable.groups,
            key=lambda group: (
                group.id != chosen,
                _quality(store, [group], soft_blocked),
                natural_key(group.code),
            ),
        )
        # Pinned groups are never dropped; every other slot may stay empty.
        candidates[index] = ranked if index in pinned_indices else [*ranked, None]

    assignment: dict[int, Group | None] = {}
    used: set[str] = set()
    best_cost: tuple[int, int, int] | None = None
    solutions: list[list[Group | None]] = []
    nodes = 0
    exhausted = True
    deadline = time.monotonic() + time_limit

    def dfs(position: int, cost: tuple[int, int, int]) -> None:
        nonlocal nodes, exhausted, best_cost, solutions
        if nodes >= node_limit or (nodes % 256 == 0 and time.monotonic() > deadline):
            exhausted = False
            return
        if position == len(order):
            if best_cost is None or cost < best_cost:
                best_cost, solutions = cost, []
            if len(solutions) < max_solutions:
                solutions.append([assignment[index] for index in range(len(variables))])
            return

        variable_index = order[position]
        for group in candidates[variable_index]:
            nodes += 1
            if nodes >= node_limit:
                exhausted = False
                return
            if group is not None and not can_place(group, used):
                continue
            next_cost = add(cost, option_cost(variable_index, group))
            if best_cost is not None and next_cost > best_cost:
                continue
            assignment[variable_index] = group
            if group is not None:
                used.add(group.id)
            bound = add(next_cost, lower_bound(order[position + 1 :], used))
            if best_cost is None or bound < best_cost or (
                bound == best_cost and len(solutions) < max_solutions
            ):
                dfs(position + 1, next_cost)
            if group is not None:
                used.discard(group.id)
            del assignment[variable_index]
            if not exhausted:
                return

    dfs(0, (0, 0, 0))

    meta = {
        "nodes": nodes,
        "solutions_found": len(solutions),
        "node_limit": node_limit,
        "max_solutions": max_solutions,
        "time_limit": time_limit,
        "exhausted": exhausted,
    }
    if not solutions or best_cost is None:
        return None, meta
    best = min(
        solutions,
        key=lambda groups: _quality(store, [group for group in groups if group], soft_blocked),
    )
    removed, changed, unfilled = best_cost
    meta.update({"removed": removed, "changed": changed, "unfilled": unfilled})
    return best, meta


def optimize_schedule(
    store: CatalogStore,
    group_ids: list[str],
    subject_ids: list[str],
    blocked: list[dict] | None = None,
    custom_events: list[dict] | None = None,
    pinned_group_ids: list[str] | None = None,
    max_solutions: int = 60,
    node_limit: int = 200_000,
) -> dict:
    """Repair the user's own timetable instead of generating a new one.

    Overlaps between classes and with custom events are removed by changing
    the fewest possible groups; everything that does not clash stays as the
    user left it.  ``blocked`` preferences only break ties between repairs.
    """

    blocked_norm = normalize_blocked(blocked)
    events_norm = normalize_events(custom_events)
    pinned = list(dict.fromkeys(pinned_group_ids or []))
    requested = list(dict.fromkeys(subject_ids))
    groups_now = resolve_groups(store, list(dict.fromkeys(group_ids)))

    for group_id in pinned:
        if group_id not in store.group_by_id:
            raise ValueError(f"Grupo fijado desconocido: {group_id}")
    # A pinned group is always part of the timetable, even if the client did
    # not send it among the selected groups.
    groups_now += [
        store.group_by_id[group_id]
        for group_id in pinned
        if group_id not in {group.id for group in groups_now}
    ]
    for group in groups_now:
        if group.subject_id not in requested:
            raise ValueError(f"El grupo {group.code} no pertenece a ninguna materia de la matrícula")

    # One variable per selected group, so CLE01 + CLE02 of the same subject
    # are two classes to keep, not one slot where the last pick wins.  A
    # subject type with nothing selected still gets one empty variable.
    variables: list[Variable] = []
    current: list[str | None] = []
    pinned_indices: set[int] = set()
    pinned_set = set(pinned)
    for base in _build_variables(store, requested):
        chosen = [
            group for group in groups_now
            if group.subject_id == base.subject_id and group.type == base.type
        ]
        if not chosen:
            variables.append(base)
            current.append(None)
            continue
        for group in chosen:
            if group.id in pinned_set:
                pinned_indices.add(len(variables))
                variables.append(Variable(base.subject_id, base.type, [group]))
            else:
                variables.append(base)
            current.append(group.id)

    repaired, meta = _repair_solution(
        store,
        variables,
        current,
        pinned_indices,
        events_norm,
        blocked_norm,
        max_solutions,
        node_limit,
    )

    if repaired is None:
        status = "impossible"
        final_ids = [group.id for group in groups_now]
    else:
        final_ids = [group.id for group in repaired if group is not None]
        if meta.get("removed"):
            status = "partial"
        elif set(final_ids) == {group_id for group_id in current if group_id}:
            status = "unchanged"
        else:
            status = "optimized"

    result = validate_selection(
        store,
        final_ids,
        blocked=blocked_norm,
        day_start=store.catalog.term.day_start,
        day_end=store.catalog.term.day_end,
        subject_ids=requested,
        custom_events=events_norm,
    )
    changes = []
    for index, variable in enumerate(variables):
        before = current[index]
        after = before if repaired is None else (repaired[index].id if repaired[index] else None)
        if before == after:
            continue
        changes.append(
            {
                "subject_id": variable.subject_id,
                "subject_name": store.subject_by_id[variable.subject_id].name,
                "type": variable.type.value,
                "type_label": GROUP_TYPE_LABELS[variable.type],
                "from_group_id": before,
                "from_group_code": store.group_by_id[before].code if before else None,
                "to_group_id": after,
                "to_group_code": store.group_by_id[after].code if after else None,
            }
        )
    result["status"] = status
    result["solver"] = {"status": status, **meta}
    result["pinned_group_ids"] = pinned
    result["requested_subject_ids"] = requested
    result["group_ids"] = final_ids
    result["changes"] = changes
    return result


def free_slots_for_groups(
    store: CatalogStore,
    group_ids: list[str],
    ignore_session_ids: list[str] | None = None,
    blocked: list[dict] | None = None,
    day_start: str = "08:00",
    day_end: str = "21:00",
    min_duration: int = 60,
    custom_events: list[dict] | None = None,
) -> dict:
    groups = resolve_groups(store, group_ids)
    ignored = set(ignore_session_ids or [])
    sessions = sessions_for_groups(store, groups, ignored)
    blocked_norm = normalize_blocked(blocked)
    events_norm = normalize_events(custom_events)
    views = [store.session_view(session) for session in sessions]
    slots = free_intervals(
        views + events_norm,
        blocked=blocked_norm,
        day_start=day_start,
        day_end=day_end,
        min_duration=min_duration,
    )
    return {
        "group_ids": [group.id for group in groups],
        "ignore_session_ids": sorted(ignored),
        "day_start": day_start,
        "day_end": day_end,
        "min_duration": min_duration,
        "slots": slots,
        "occupied": views,
        "custom_events": events_norm,
    }
