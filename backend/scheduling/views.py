"""Operations on session views (``store.session_view`` dicts): overlaps,
free time and merging of consecutive meetings into blocks."""

from __future__ import annotations

from ..timeutil import DAY_LABELS, format_hm
from .common import DAYS, _day_index, _minutes, share_week


def _conflict_side(view: dict) -> dict:
    return {
        "id": view["id"],
        "subject_name": view["subject_name"],
        "group_code": view["group_code"],
        "start": view["start"],
        "end": view["end"],
    }


def _can_clash(first: dict, second: dict) -> bool:
    if first["day"] != second["day"]:
        return False
    if first.get("semester", 1) != second.get("semester", 1):
        return False
    # A group meeting in two rooms at once is one class, not a clash.
    if first.get("group_id") and first.get("group_id") == second.get("group_id"):
        return False
    # Meetings in disjoint sets of weeks never coincide.
    return share_week(first.get("weeks"), second.get("weeks"))


def find_conflicts(views: list[dict]) -> list[dict]:
    """Every overlap is reported once as an unordered pair."""

    conflicts: list[dict] = []
    for index, first in enumerate(views):
        for second in views[index + 1 :]:
            if not _can_clash(first, second):
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
                        "left": _conflict_side(first),
                        "right": _conflict_side(second),
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


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[list[int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


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
        if view["day"] in occupied:
            occupied[view["day"]].append((_minutes(view["start"]), _minutes(view["end"])))
    for item in blocked or []:
        if item["day"] in occupied:
            occupied[item["day"]].append((item["start_min"], item["end_min"]))

    result: list[dict] = []
    for day in DAYS:
        cursor = window_start
        gaps: list[tuple[int, int]] = []
        for start, end in _merge_intervals(occupied[day]):
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


def _start_block(view: dict) -> dict:
    return {
        **view,
        "id": f"blk-{view['id']}",
        "session_ids": [view["id"]],
        "conflict_with": list(view.get("conflict_with", [])),
        "has_conflict": bool(view.get("has_conflict")),
    }


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
            tuple(view["weeks"]) if view.get("weeks") is not None else None,
        )
        groups.setdefault(key, []).append(view)

    blocks: list[dict] = []
    for group_views in groups.values():
        group_views.sort(key=lambda item: _minutes(item["start"]))
        current: dict | None = None
        for view in group_views:
            if current is not None and _minutes(current["end"]) == _minutes(view["start"]):
                current["end"] = view["end"]
                current["duration_minutes"] += view["duration_minutes"]
                current["session_ids"].append(view["id"])
                current["has_conflict"] = current["has_conflict"] or bool(view.get("has_conflict"))
                current["conflict_with"] = list(
                    dict.fromkeys(current["conflict_with"] + list(view.get("conflict_with", [])))
                )
                continue
            if current is not None:
                blocks.append(current)
            current = _start_block(view)
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
