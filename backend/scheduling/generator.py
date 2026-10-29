"""Automatic timetable generator (``/api/schedules/generate``).

A small constraint solver: one variable per (subject, group type), one value
per group, and the constraint that no two weekly sessions overlap.  It is no
longer used by the UI (which repairs the student's own timetable, see
``repair.py``) but remains in the API.
"""

from __future__ import annotations

from ..ids import natural_key
from ..models import GROUP_TYPE_LABELS, ClassSession, Group, GroupType
from ..store import CatalogStore
from .common import _overlaps, _overlaps_blocked, normalize_blocked, normalize_events
from .validation import validate_selection
from .variables import Variable, _apply_pins, _build_variables, _quality


def _plan_years(store: CatalogStore, subject_ids: list[str]) -> list[dict]:
    year_ids = list(dict.fromkeys(store.subject_by_id[subject_id].year_id for subject_id in subject_ids))
    return [store.year_summary(store.year_by_id[year_id]) for year_id in year_ids]


def _subject_origin(store: CatalogStore, subject_id: str) -> dict:
    """Year and degree a subject comes from, as shown next to its name."""

    subject = store.subject_by_id[subject_id]
    year = store.year_by_id[subject.year_id]
    return {
        "year_id": subject.year_id,
        "year_name": year.name,
        "degree_short_name": store.degree_by_id[year.degree_id].short_name,
    }


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
        return all(not any(_overlaps(session, other) for other in used) for session in group_sessions)

    # Fail-first variable order, and compact candidates first.
    order = sorted(
        range(len(variables)),
        key=lambda index: (
            len(variables[index].groups),
            -sum(len(store.sessions_by_group[group.id]) for group in variables[index].groups[:1]),
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
        if len(solutions) >= max_solutions or nodes >= node_limit:
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

            # Forward check: every remaining variable must still have a value.
            supported = all(
                any(can_place(candidate, used) for candidate in candidates[remaining])
                for remaining in order[position + 1 :]
            )
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
        assignment[index] = min(
            variable.groups,
            key=lambda group: (
                _quality(store, list(assignment.values()) + [group], blocked),
                natural_key(group.code),
            ),
        )

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
    return (min(by_type.values()) if by_type else 0, len(groups), original_index)


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

    def first_fit(ids: list[str]) -> list[Group] | None:
        variables = _apply_pins(store, _build_variables(store, ids), pinned_group_ids)
        groups, _ = _strict_solution(store, variables, blocked, max_solutions=1, node_limit=min(node_limit, 30_000))
        return groups

    pinned_subjects = {
        store.group_by_id[group_id].subject_id
        for group_id in pinned_group_ids or []
        if group_id in store.group_by_id
    }
    included: list[str] = [subject_id for subject_id in subject_ids if subject_id in pinned_subjects]
    current: list[Group] = []
    if included:
        groups = first_fit(included)
        if groups is None:
            return [], [], {"subset_attempts": len(order), "pinned_infeasible": True}
        current = groups

    for subject_id in order:
        if subject_id in pinned_subjects:
            continue
        trial = included + [subject_id]
        groups = first_fit(trial)
        if groups is not None:
            included = trial
            current = groups

    if not included:
        return [], [], {"subset_attempts": len(order)}

    ordered_included = [subject_id for subject_id in subject_ids if subject_id in included]
    variables = _apply_pins(store, _build_variables(store, ordered_included), pinned_group_ids)
    groups, meta = _strict_solution(store, variables, blocked, max_solutions=60, node_limit=node_limit)
    return ordered_included, groups or current, {"subset_attempts": len(order), **meta}


def _best_subset(
    store: CatalogStore,
    requested: list[str],
    solver_blocked: list[dict],
    blocked_norm: list[dict],
    node_limit: int,
    pinned: list[str],
) -> tuple[list[str], list[Group], dict] | None:
    """The largest conflict-free subset of the plan, trying three orders."""

    plan_order = {subject_id: index for index, subject_id in enumerate(requested)}
    orders = [
        sorted(
            requested,
            key=lambda subject_id: _subject_flexibility(store, subject_id, plan_order.get(subject_id, 0)),
        ),
        requested,
        list(reversed(requested)),
    ]
    candidates = []
    for order in orders:
        candidate = _greedy_subset(store, requested, order, solver_blocked, node_limit, pinned_group_ids=pinned)
        if candidate[0]:
            candidates.append(candidate)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            len(item[0]),
            tuple(-value for value in _quality(store, item[1], blocked_norm)),
            -len([session for group in item[1] for session in store.sessions_by_group[group.id]]),
        ),
    )


def _requested_subjects(
    store: CatalogStore,
    year_id: str | None,
    subject_ids: list[str] | None,
) -> tuple[list[str], dict | None]:
    """The plan to generate and, for a single year, that year's data."""

    if subject_ids is None:
        if year_id is None:
            raise ValueError("Indica year_id o subject_ids")
        year = store.year_by_id.get(year_id)
        if year is None:
            raise KeyError(year_id)
        return list(year.subject_ids), year.model_dump(mode="json")
    requested = list(dict.fromkeys(subject_ids))
    year_data = None
    if year_id is not None:
        year = store.year_by_id.get(year_id)
        if year is None:
            raise KeyError(year_id)
        if set(requested) <= set(year.subject_ids):
            year_data = year.model_dump(mode="json")
    return requested, year_data


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
    # Extracurricular activities are occupied time, like blocked slots.
    solver_blocked = blocked_norm + events_norm
    pinned = list(dict.fromkeys(pinned_group_ids or []))
    requested, year_data = _requested_subjects(store, year_id, subject_ids)

    variables = _apply_pins(store, _build_variables(store, requested), pinned)
    best, meta = _strict_solution(store, variables, solver_blocked, max_solutions, node_limit)

    included = requested
    excluded: list[str] = []
    if best is not None:
        status, groups = "optimal", best
        solver = {"status": status, **meta}
    else:
        subset_result = None
        if allow_partial and len(requested) > 1:
            subset_result = _best_subset(store, requested, solver_blocked, blocked_norm, node_limit, pinned)
        if subset_result is not None:
            included, groups, subset_meta = subset_result
            excluded = [subject_id for subject_id in requested if subject_id not in included]
            status = "partial"
            solver = {"status": status, **meta, "subset": subset_meta}
            # The assignments below must describe the subset, not the whole plan.
            variables = _apply_pins(store, _build_variables(store, included), pinned)
        elif allow_conflicts:
            groups, relaxed_meta = _relaxed_solution(store, variables, solver_blocked)
            status = "relaxed"
            solver = {"status": status, **meta, "relaxed": relaxed_meta}
        else:
            groups, status = [], "impossible"
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
    result["degrees"] = [store.degree_by_id[degree_id].model_dump(mode="json") for degree_id in degree_ids]
    result["requested_subject_ids"] = requested
    result["excluded_subjects"] = [
        {"id": subject_id, "name": store.subject_by_id[subject_id].name, **_subject_origin(store, subject_id)}
        for subject_id in excluded
    ]
    result["assignments"] = (
        [
            {
                "subject_id": variable.subject_id,
                "subject_name": store.subject_by_id[variable.subject_id].name,
                **_subject_origin(store, variable.subject_id),
                "type": variable.type.value,
                "type_label": GROUP_TYPE_LABELS[variable.type],
                "group_id": groups[index].id,
                "group_code": groups[index].code,
            }
            for index, variable in enumerate(variables)
            if index < len(groups)
        ]
        if groups
        else []
    )
    return result
