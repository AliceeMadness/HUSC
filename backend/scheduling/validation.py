"""Validation of a selection of groups and free time of a set of groups."""

from __future__ import annotations

from ..ids import natural_key
from ..models import GROUP_TYPE_LABELS, GROUP_TYPE_ORDER, ClassSession, Group
from ..store import CatalogStore
from ..timeutil import DAY_LABELS, format_hm
from .common import (
    DAY_ORDER,
    _day_index,
    _minutes,
    in_week,
    normalize_blocked,
    normalize_events,
    resolve_groups,
    sessions_for_groups,
    share_week,
)
from .views import find_conflicts, free_intervals, merge_session_views


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


def _subjects_to_check(
    store: CatalogStore,
    groups: list[Group],
    subject_ids: list[str] | None,
) -> list[str]:
    if subject_ids is not None:
        # An explicit plan (matrícula) is the source of truth, even when the
        # student has not selected a group yet.  This is what makes repeated
        # courses and double degrees work.
        check_ids = list(dict.fromkeys(subject_ids))
        unknown = [subject_id for subject_id in check_ids if subject_id not in store.subject_by_id]
        if unknown:
            raise ValueError(f"Asignaturas desconocidas: {', '.join(unknown)}")
        return check_ids
    if not groups:
        return []
    selected_subject_ids = list(dict.fromkeys(group.subject_id for group in groups))
    years = {store.subject_by_id[subject_id].year_id for subject_id in selected_subject_ids}
    # Backwards-compatible inference: a single-year selection means "all
    # subjects of that year"; a mixed selection only checks what was chosen.
    if len(years) == 1:
        return list(store.year_by_id[next(iter(years))].subject_ids)
    return selected_subject_ids


def _missing_requirements(
    store: CatalogStore,
    groups: list[Group],
    subject_ids: list[str] | None = None,
) -> list[dict]:
    missing = []
    for subject_id in _subjects_to_check(store, groups, subject_ids):
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
                    "available_group_ids": [group.id for group in all_groups if group.type == group_type],
                }
            )
    return missing


def _selection_view(store: CatalogStore, groups: list[Group]) -> list[dict]:
    result = []
    for subject_id in dict.fromkeys(group.subject_id for group in groups):
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
            if view["day"] != event["day"] or not share_week(view.get("weeks"), event.get("weeks")):
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
                    "overlap_minutes": min(end, event["end_min"]) - max(start, event["start_min"]),
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
    week: str | None = None,
) -> dict:
    """Overlaps over the whole term; blocks, free slots and stats of ``week``.

    Conflicts are reported whenever two meetings share any week, so a clash
    in a single special week is not hidden by looking at another one.
    """

    groups = resolve_groups(store, group_ids)
    blocked_norm = normalize_blocked(blocked)
    events_norm = normalize_events(custom_events)
    if subject_ids is not None:
        plan_set = set(subject_ids)
        outside_plan = [group.id for group in groups if group.subject_id not in plan_set]
        if outside_plan:
            raise ValueError(
                "Hay grupos de asignaturas que no están en la matrícula: " + ", ".join(outside_plan)
            )
    sessions = sessions_for_groups(store, groups)
    all_views, conflicts, _ = _enriched_sessions(store, sessions)
    views = in_week(all_views, week)
    blocks = merge_session_views(views)
    blocked_conflicts = _blocked_conflicts(all_views, blocked_norm)
    event_conflicts = _event_conflicts(all_views, events_norm)
    free = free_intervals(
        views + [event for event in in_week(events_norm, week) if event["day"] in DAY_ORDER],
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
        "week": week,
        "sessions": views,
        "blocks": blocks,
        "conflicts": conflicts,
        "blocked_conflicts": blocked_conflicts,
        "event_conflicts": event_conflicts,
        "custom_events": events_norm,
        "free_slots": free,
        "stats": stats,
    }


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
