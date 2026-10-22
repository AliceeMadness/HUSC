"""Scheduling algorithm tests.

No external services: these run against the generated catalogue and complete in
well under a second per year.
"""

from __future__ import annotations

import pytest

from backend.scheduling import (
    _quality,
    find_conflicts,
    free_intervals,
    free_slots_for_groups,
    generate_schedule,
    validate_selection,
)
from backend.store import CatalogStore, SHARED_DIR

store = CatalogStore()


def _year_groups(year_id: str) -> list[str]:
    year = store.year_by_id[year_id]
    return [
        group_id
        for subject_id in year.subject_ids
        for group_id in store.subject_by_id[subject_id].group_ids
    ]


def test_generate_full_first_year() -> None:
    result = generate_schedule(store, "year-matematicas-1")
    assert result["status"] == "optimal"
    assert result["stats"]["conflict_count"] == 0
    assert result["stats"]["blocked_conflict_count"] == 0
    assert len(result["group_ids"]) == 8
    assert len(result["assignments"]) == 8
    assert result["excluded_subjects"] == []
    assert {item["type"] for item in result["assignments"]} == {
        "expositive",
        "laboratory",
    }


@pytest.mark.parametrize(
    "year_id",
    [
        "year-matematicas-2",
        "year-matematicas-3",
        "year-matematicas-4",
        "year-matematicas-fisica-1",
        "year-matematicas-fisica-2",
        "year-matematicas-fisica-3",
        "year-matematicas-fisica-5",
    ],
)
def test_generate_other_years_without_conflicts(year_id: str) -> None:
    result = generate_schedule(store, year_id)
    assert result["status"] == "optimal"
    assert result["stats"]["conflict_count"] == 0
    assert result["excluded_subjects"] == []


def test_fourth_year_double_degree_returns_best_subset() -> None:
    result = generate_schedule(store, "year-matematicas-fisica-4")
    assert result["status"] == "partial"
    assert result["stats"]["conflict_count"] == 0
    assert result["excluded_subjects"]
    assert len(result["group_ids"]) < len(_year_groups("year-matematicas-fisica-4"))
    # The partial result must clearly tell the UI which subjects did not fit.
    assert all({"id", "name"} <= subject.keys() for subject in result["excluded_subjects"])
    assert len(result["requested_subject_ids"]) == 10


def test_generate_only_requested_subjects() -> None:
    subject_id = store.year_by_id["year-matematicas-1"].subject_ids[0]
    result = generate_schedule(store, "year-matematicas-1", subject_ids=[subject_id])
    assert result["status"] == "optimal"
    assert all(item["subject_id"] == subject_id for item in result["assignments"])
    assert len(result["group_ids"]) == 2  # expositiva + laboratorio


def test_preferences_are_respected() -> None:
    blocked = [{"day": "fri", "start": "08:00", "end": "21:00"}] + [
        {"day": day, "start": "08:00", "end": "09:00"}
        for day in ("mon", "tue", "wed", "thu", "fri")
    ]
    result = generate_schedule(store, "year-matematicas-1", blocked=blocked)
    assert result["stats"]["conflict_count"] == 0
    assert result["stats"]["blocked_conflict_count"] == 0
    assert all(session["day"] != "fri" for session in result["sessions"])
    assert all(session["start"] != "08:00" for session in result["sessions"])
    # Free slots must not include the blocked windows either.
    assert all(
        not (slot["day"] == "fri" or (slot["start"] == "08:00" and slot["end"] == "09:00"))
        for slot in result["free_slots"]
    )


def test_validate_finds_conflicts_and_missing_groups() -> None:
    # Pick one group of each type for one subject; deliberately omit the rest.
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    group_ids = []
    seen_types = set()
    for group_id in subject.group_ids:
        group = store.group_by_id[group_id]
        if group.type not in seen_types:
            group_ids.append(group_id)
            seen_types.add(group.type)
    result = validate_selection(store, group_ids)
    assert result["group_ids"] == group_ids
    assert result["missing"]  # other subjects have no groups selected
    assert result["stats"]["total_sessions"] > 0


def test_find_conflicts_reports_overlaps() -> None:
    first = {
        "id": "a",
        "subject_name": "A",
        "group_code": "CLE01",
        "day": "mon",
        "day_label": "Lunes",
        "start": "09:00",
        "end": "10:30",
    }
    second = {
        "id": "b",
        "subject_name": "B",
        "group_code": "CLE02",
        "day": "mon",
        "day_label": "Lunes",
        "start": "10:00",
        "end": "11:00",
    }
    conflicts = find_conflicts([first, second])
    assert len(conflicts) == 1
    assert conflicts[0]["start"] == "10:00"
    assert conflicts[0]["end"] == "10:30"
    assert conflicts[0]["overlap_minutes"] == 30


def test_free_intervals_complement_and_merge() -> None:
    occupied = [
        {"day": "mon", "start": "09:00", "end": "10:00"},
        {"day": "mon", "start": "10:30", "end": "11:00"},
    ]
    slots = free_intervals(occupied, day_start="08:00", day_end="12:00", min_duration=30)
    monday = [slot for slot in slots if slot["day"] == "mon"]
    assert monday == [
        {"day": "mon", "day_label": "Lunes", "start": "08:00", "end": "09:00", "duration_minutes": 60},
        {"day": "mon", "day_label": "Lunes", "start": "10:00", "end": "10:30", "duration_minutes": 30},
        {"day": "mon", "day_label": "Lunes", "start": "11:00", "end": "12:00", "duration_minutes": 60},
    ]


def test_free_slots_for_groups_ignores_moved_session() -> None:
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    group_id = subject.group_ids[0]
    sessions = store.sessions_by_group[group_id]
    assert sessions
    without = free_slots_for_groups(
        store,
        [group_id],
        ignore_session_ids=[sessions[0].id],
        day_start="08:00",
        day_end="21:00",
        min_duration=60,
    )
    assert all(
        session["id"] != sessions[0].id for session in without["occupied"]
    )
    assert without["slots"]


def test_validate_blocked_conflicts() -> None:
    group = store.group_by_id[
        store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]].group_ids[0]
    ]
    session = store.sessions_by_group[group.id][0]
    result = validate_selection(
        store,
        [group.id],
        blocked=[{"day": session.day.value, "start": session.start, "end": session.end}],
    )
    assert result["blocked_conflicts"]
    assert result["ok"] is False


def test_unknown_group_raises() -> None:
    with pytest.raises(ValueError):
        validate_selection(store, ["grp-does-not-exist"])


def test_quality_prefers_compact_schedule() -> None:
    group_id = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]].group_ids[0]
    group = store.group_by_id[group_id]
    assert _quality(store, [group], [])[0] == 0


def test_shared_schedule_roundtrip() -> None:
    group_ids = _year_groups("year-matematicas-1")[:2]
    record = store.save_shared("year-matematicas-1", group_ids)
    try:
        loaded = store.load_shared(record.id)
        assert loaded.group_ids == group_ids
        assert loaded.year_id == "year-matematicas-1"
        assert [selection.group_id for selection in loaded.selections] == group_ids
        assert (SHARED_DIR / f"{record.id}.json").exists()
    finally:
        (SHARED_DIR / f"{record.id}.json").unlink(missing_ok=True)


def test_shared_schedule_rejects_other_year_groups() -> None:
    other = _year_groups("year-matematicas-2")[:1]
    with pytest.raises(ValueError):
        store.save_shared("year-matematicas-1", other)
