"""Search model shared by the generator and the repair.

One variable per (subject, group type) whose values are that subject's
groups of that type, and ``_quality`` to rank complete timetables.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ids import natural_key
from ..models import GROUP_TYPE_ORDER, Group, GroupType
from ..store import CatalogStore
from .common import _minutes, _overlaps, _overlaps_blocked


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
        by_day.setdefault(session.day.value, []).append((_minutes(session.start), _minutes(session.end)))
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
            raise ValueError(f"No se pueden fijar dos grupos del mismo tipo: {previous.code} y {group.code}")
        pinned_by_key[key] = group

    present_keys = {variable.key for variable in variables}
    missing = [key for key in pinned_by_key if key not in present_keys]
    if missing:
        labels = [f"{store.subject_by_id[subject_id].name} ({group_type.value})" for subject_id, group_type in missing]
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
