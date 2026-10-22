"""Conflict detection, free-slot search and the timetable generator.

All algorithms operate on the canonical catalogue and return plain JSON-ready
dictionaries.  The generator is a small constraint solver: one variable per
(subject, group type), one value per group, and the constraint that no two
weekly sessions overlap.
"""

from __future__ import annotations

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
) -> dict:
    groups = resolve_groups(store, group_ids)
    blocked_norm = normalize_blocked(blocked)
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
    blocked_conflicts = _blocked_conflicts(views, blocked_norm)
    free = free_intervals(
        views,
        blocked=blocked_norm,
        day_start=day_start,
        day_end=day_end,
        min_duration=min_free_minutes,
    )
    return {
        "ok": len(conflicts) == 0 and len(blocked_conflicts) == 0,
        "group_ids": [group.id for group in groups],
        "subject_ids": list(dict.fromkeys(subject_ids)) if subject_ids is not None else None,
        "selections": _selection_view(store, groups),
        "missing": _missing_requirements(store, groups, subject_ids=subject_ids),
        "sessions": views,
        "conflicts": conflicts,
        "blocked_conflicts": blocked_conflicts,
        "free_slots": free,
        "stats": _stats(views, conflicts, blocked_conflicts),
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
            if _overlaps(first, second):
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
) -> tuple[list[str], list[Group], dict]:
    """Add subjects one by one, keeping only those that still fit.

    Fourth-year degrees contain electives that share time slots, and custom
    plans can mix very different years.  Rather than returning a schedule full
    of overlaps, we return the largest subset we can fit conflict-free and tell
    the UI which subjects were left out.
    """

    included: list[str] = []
    current: list[Group] = []
    for subject_id in order:
        trial = included + [subject_id]
        variables = _build_variables(store, trial)
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
    variables = _build_variables(store, ordered_included)
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
) -> dict:
    """Generate a timetable for a year (legacy) or an arbitrary plan.

    ``subject_ids`` is the new contract: it may mix subjects from several
    academic years and degrees.  ``year_id`` remains as a convenience for
    single-year plans and old API clients.
    """

    blocked_norm = normalize_blocked(blocked)
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

    variables = _build_variables(store, requested)
    best, meta = _strict_solution(store, variables, blocked_norm, max_solutions, node_limit)

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
                candidate = _greedy_subset(store, requested, order, blocked_norm, node_limit)
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
            variables = _build_variables(store, included)
        elif allow_conflicts:
            groups, relaxed_meta = _relaxed_solution(store, variables, blocked_norm)
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
    )
    result["status"] = status
    result["solver"] = solver
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


def free_slots_for_groups(
    store: CatalogStore,
    group_ids: list[str],
    ignore_session_ids: list[str] | None = None,
    blocked: list[dict] | None = None,
    day_start: str = "08:00",
    day_end: str = "21:00",
    min_duration: int = 60,
) -> dict:
    groups = resolve_groups(store, group_ids)
    ignored = set(ignore_session_ids or [])
    sessions = sessions_for_groups(store, groups, ignored)
    blocked_norm = normalize_blocked(blocked)
    views = [store.session_view(session) for session in sessions]
    slots = free_intervals(
        views,
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
    }
