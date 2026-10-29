"""Overlap repair ("Resolver solapes", ``/api/schedules/optimize``).

Unlike the generator, the repair starts from the student's own groups and
changes as few of them as possible.
"""

from __future__ import annotations

import time

from ..ids import natural_key
from ..models import GROUP_TYPE_LABELS, Group
from ..store import CatalogStore
from .common import _overlaps, _overlaps_blocked, normalize_blocked, normalize_events, resolve_groups
from .validation import validate_selection
from .variables import Variable, _build_variables, _quality

Cost = tuple[int, int, int]


def _add(a: Cost, b: Cost) -> Cost:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _clash_index(
    store: CatalogStore,
    variables: list[Variable],
    hard_blocked: list[dict],
) -> tuple[set[str], dict[str, set[str]]]:
    """Groups that hit hard-blocked time, and which groups clash pairwise.

    Precomputed so that each step of the search is a set test.
    """

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
    return blocked_ids, clashes


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

    blocked_ids, clashes = _clash_index(store, variables, hard_blocked)

    def can_place(group: Group, used: set[str]) -> bool:
        return group.id not in blocked_ids and group.id not in used and not (clashes[group.id] & used)

    def option_cost(index: int, group: Group | None) -> Cost:
        chosen = current[index]
        if group is None:
            return (1, 0, 0) if chosen else (0, 0, 1)
        if chosen and chosen != group.id:
            return (0, 1, 0)
        return (0, 0, 0)

    def lower_bound(remaining: list[int], used: set[str]) -> Cost:
        bound = (0, 0, 0)
        for index in remaining:
            chosen = current[index]
            placeable = [group for group in variables[index].groups if can_place(group, used)]
            if not placeable:
                bound = _add(bound, option_cost(index, None))
            elif chosen and chosen not in {group.id for group in placeable}:
                bound = _add(bound, (0, 1, 0))
        return bound

    # Fail-first: variables with fewer alternatives are decided first.
    order = sorted(range(len(variables)), key=lambda index: (len(variables[index].groups), index))
    candidates: dict[int, list[Group | None]] = {}
    for index, variable in enumerate(variables):
        chosen = current[index]
        ranked: list[Group | None] = sorted(
            variable.groups,
            key=lambda group: (group.id != chosen, _quality(store, [group], soft_blocked), natural_key(group.code)),
        )
        # Pinned groups are never dropped; every other slot may stay empty.
        candidates[index] = ranked if index in pinned_indices else [*ranked, None]

    assignment: dict[int, Group | None] = {}
    used: set[str] = set()
    best_cost: Cost | None = None
    solutions: list[list[Group | None]] = []
    nodes = 0
    exhausted = True
    deadline = time.monotonic() + time_limit

    def dfs(position: int, cost: Cost) -> None:
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
            next_cost = _add(cost, option_cost(variable_index, group))
            if best_cost is not None and next_cost > best_cost:
                continue
            assignment[variable_index] = group
            if group is not None:
                used.add(group.id)
            bound = _add(next_cost, lower_bound(order[position + 1 :], used))
            if best_cost is None or bound < best_cost or (bound == best_cost and len(solutions) < max_solutions):
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
    best = min(solutions, key=lambda groups: _quality(store, [group for group in groups if group], soft_blocked))
    removed, changed, unfilled = best_cost
    meta.update({"removed": removed, "changed": changed, "unfilled": unfilled})
    return best, meta


def _repair_variables(
    store: CatalogStore,
    requested: list[str],
    groups_now: list[Group],
    pinned: list[str],
) -> tuple[list[Variable], list[str | None], set[int]]:
    """One variable per selected group, so CLE01 + CLE02 of the same subject
    are two classes to keep, not one slot where the last pick wins.  Only
    what the user chose is repaired: a subject type with nothing selected
    (say, the CLIL of a subject where only CLE was picked) is left alone."""

    variables: list[Variable] = []
    current: list[str | None] = []
    pinned_indices: set[int] = set()
    pinned_set = set(pinned)
    for base in _build_variables(store, requested):
        chosen = [group for group in groups_now if group.subject_id == base.subject_id and group.type == base.type]
        for group in chosen:
            if group.id in pinned_set:
                pinned_indices.add(len(variables))
                variables.append(Variable(base.subject_id, base.type, [group]))
            else:
                variables.append(base)
            current.append(group.id)
    return variables, current, pinned_indices


def _describe_changes(
    store: CatalogStore,
    variables: list[Variable],
    current: list[str | None],
    repaired: list[Group | None] | None,
) -> list[dict]:
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
    return changes


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
    selected_ids = {group.id for group in groups_now}
    groups_now += [store.group_by_id[group_id] for group_id in pinned if group_id not in selected_ids]
    for group in groups_now:
        if group.subject_id not in requested:
            raise ValueError(f"El grupo {group.code} no pertenece a ninguna materia de la matrícula")

    variables, current, pinned_indices = _repair_variables(store, requested, groups_now, pinned)
    repaired, meta = _repair_solution(
        store, variables, current, pinned_indices, events_norm, blocked_norm, max_solutions, node_limit
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
    result["status"] = status
    result["solver"] = {"status": status, **meta}
    result["pinned_group_ids"] = pinned
    result["requested_subject_ids"] = requested
    result["group_ids"] = final_ids
    result["changes"] = _describe_changes(store, variables, current, repaired)
    return result
