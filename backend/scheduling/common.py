"""Shared helpers: input normalisation, group lookups and the overlap rule.

The overlap rule (``_overlaps`` / ``find_conflicts``) has a twin in
``frontend/js/schedule.js``: same day, intervals that cut ``[start, end)``,
same term and at least one common ISO week.  Change both together.
"""

from __future__ import annotations

from ..models import ClassSession, Group
from ..store import CatalogStore
from ..timeutil import parse_hm

DAYS = ("mon", "tue", "wed", "thu", "fri")
DAY_ORDER = {day: index for index, day in enumerate(DAYS)}


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
                "weeks": event.get("weeks"),
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


def share_week(first: list[str] | None, second: list[str] | None) -> bool:
    """Whether two meetings happen in a common week (``None`` = every week)."""

    return first is None or second is None or not set(first).isdisjoint(second)


def _overlaps(a: ClassSession, b: ClassSession) -> bool:
    return (
        a.day == b.day
        and a.semester == b.semester
        and _minutes(a.start) < _minutes(b.end)
        and _minutes(b.start) < _minutes(a.end)
        and share_week(a.weeks, b.weeks)
    )


def in_week(views: list[dict], week: str | None) -> list[dict]:
    """The meetings of one ISO week; ``None`` keeps them all."""

    if week is None:
        return views
    return [view for view in views if view.get("weeks") is None or week in view["weeks"]]


def _overlaps_blocked(session: ClassSession, blocked: list[dict]) -> bool:
    """Whether a class falls in blocked time or an extracurricular activity
    (only those of its weeks: blocked slots and weekly activities have none)."""

    start, end = _minutes(session.start), _minutes(session.end)
    return any(
        item["day"] == session.day.value
        and start < item["end_min"]
        and item["start_min"] < end
        and share_week(session.weeks, item.get("weeks"))
        for item in blocked
    )
