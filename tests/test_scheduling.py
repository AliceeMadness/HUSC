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
    merge_session_views,
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


def _required_groups_for_year(year_id: str) -> int:
    return sum(
        len({store.group_by_id[gid].type for gid in store.subject_by_id[sid].group_ids})
        for sid in store.year_by_id[year_id].subject_ids
    )


def test_generate_full_first_year() -> None:
    result = generate_schedule(store, "year-matematicas-1")
    assert result["status"] == "optimal"
    assert result["stats"]["conflict_count"] == 0
    assert result["stats"]["blocked_conflict_count"] == 0
    assert len(result["group_ids"]) == _required_groups_for_year("year-matematicas-1")
    assert len(result["assignments"]) == len(result["group_ids"])
    assert result["excluded_subjects"] == []


@pytest.mark.parametrize("year_id", [year.id for year in store.catalog.years])
def test_generate_every_year_without_conflicts(year_id: str) -> None:
    result = generate_schedule(store, year_id)
    assert result["status"] in {"optimal", "partial"}
    assert result["stats"]["conflict_count"] == 0
    assert result["stats"]["event_conflict_count"] == 0
    if result["status"] == "partial":
        assert result["excluded_subjects"]
    else:
        assert result["excluded_subjects"] == []


def test_fourth_year_double_degree_returns_best_subset() -> None:
    result = generate_schedule(store, "year-matematicas-fisica-4")
    assert result["status"] == "partial"
    assert result["stats"]["conflict_count"] == 0
    assert result["excluded_subjects"]
    assert len(result["group_ids"]) < len(_year_groups("year-matematicas-fisica-4"))
    # The partial result must clearly tell the UI which subjects did not fit.
    assert all({"id", "name"} <= subject.keys() for subject in result["excluded_subjects"])
    assert len(result["requested_subject_ids"]) == len(
        store.year_by_id["year-matematicas-fisica-4"].subject_ids
    )


def test_generate_only_requested_subjects() -> None:
    subject_id = store.year_by_id["year-matematicas-1"].subject_ids[0]
    result = generate_schedule(store, "year-matematicas-1", subject_ids=[subject_id])
    assert result["status"] == "optimal"
    assert all(item["subject_id"] == subject_id for item in result["assignments"])
    expected = len(
        {store.group_by_id[gid].type for gid in store.subject_by_id[subject_id].group_ids}
    )
    assert len(result["group_ids"]) == expected


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


def test_validate_empty_plan_selection_reports_missing_groups() -> None:
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    result = validate_selection(store, [], subject_ids=[subject.id])
    assert result["stats"]["total_sessions"] == 0
    assert {item["subject_id"] for item in result["missing"]} == {subject.id}
    assert len(result["missing"]) == len({group.type for group in store.group_by_id.values() if group.subject_id == subject.id})


def test_merge_consecutive_session_views() -> None:
    def view(session_id: str, start: str, end: str, room: str = "Aula 1") -> dict:
        return {
            "id": session_id,
            "subject_id": "sub-x",
            "subject_name": "Materia",
            "group_id": "grp-x",
            "group_code": "CLE01",
            "group_type": "expositive",
            "group_type_label": "Expositiva",
            "day": "mon",
            "day_label": "Lunes",
            "start": start,
            "end": end,
            "duration_minutes": 60,
            "room_id": f"room-{room}",
            "room_name": room,
            "building_id": "bld-x",
            "building_name": "Edificio",
            "has_conflict": False,
            "conflict_with": [],
        }

    blocks = merge_session_views(
        [view("a", "09:00", "10:00"), view("b", "10:00", "11:00"), view("c", "12:00", "13:00")]
    )
    assert len(blocks) == 2
    assert blocks[0]["start"] == "09:00"
    assert blocks[0]["end"] == "11:00"
    assert blocks[0]["duration_minutes"] == 120
    assert blocks[0]["session_ids"] == ["a", "b"]
    assert blocks[1]["session_ids"] == ["c"]


def test_custom_events_block_free_slots_and_are_reported() -> None:
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    group = store.group_by_id[subject.group_ids[0]]
    session = store.sessions_by_group[group.id][0]
    event = {
        "id": "evt-test",
        "title": "Trabajo",
        "day": session.day.value,
        "start": session.start,
        "end": session.end,
        "location": "Oficina",
        "color": "#7c5cbf",
    }
    result = validate_selection(store, [group.id], custom_events=[event])
    assert result["event_conflicts"]
    assert result["stats"]["event_conflict_count"] == len(result["event_conflicts"])
    assert result["ok"] is False
    assert all(
        not (
            slot["day"] == event["day"]
            and slot["start"] <= event["start"]
            and slot["end"] >= event["end"]
        )
        for slot in free_slots_for_groups(
            store, [group.id], custom_events=[event], min_duration=30
        )["slots"]
    )


def test_generator_avoids_custom_events() -> None:
    event = {
        "id": "evt-test",
        "title": "Entrenamiento",
        "day": "mon",
        "start": "17:00",
        "end": "19:00",
        "color": "#7c5cbf",
    }
    result = generate_schedule(store, "year-matematicas-1", custom_events=[event])
    assert result["stats"]["event_conflict_count"] == 0
    for session in result["sessions"]:
        assert not (
            session["day"] == event["day"]
            and session["start"] < event["end"]
            and event["start"] < session["end"]
        )


def test_pinned_group_is_kept_by_generator() -> None:
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    pinned = subject.group_ids[0]
    result = generate_schedule(store, "year-matematicas-1", pinned_group_ids=[pinned])
    assert pinned in result["group_ids"]
    assert pinned in result["pinned_group_ids"]


def test_pin_outside_plan_raises() -> None:
    subject = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    other = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    with pytest.raises(ValueError, match="fuera de la matrícula"):
        generate_schedule(
            store,
            subject_ids=[subject.id],
            pinned_group_ids=[other.group_ids[0]],
        )


def test_validate_rejects_groups_outside_the_plan() -> None:
    first = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    second = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    with pytest.raises(ValueError, match="no están en la matrícula"):
        validate_selection(store, [second.group_ids[0]], subject_ids=[first.id])


def test_quality_prefers_compact_schedule() -> None:
    group_id = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]].group_ids[0]
    group = store.group_by_id[group_id]
    assert _quality(store, [group], [])[0] == 0


def test_shared_schedule_roundtrip() -> None:
    group_ids = _year_groups("year-matematicas-1")[:2]
    subject_ids = [store.group_by_id[group_ids[0]].subject_id]
    record = store.save_shared(
        group_ids,
        subject_ids=subject_ids,
        year_id="year-matematicas-1",
        title="Primero de Matemáticas",
    )
    try:
        loaded = store.load_shared(record.id)
        assert loaded.group_ids == group_ids
        assert loaded.year_id == "year-matematicas-1"
        assert loaded.subject_ids == subject_ids
        assert loaded.title == "Primero de Matemáticas"
        assert [selection.group_id for selection in loaded.selections] == group_ids
        assert (SHARED_DIR / f"{record.id}.json").exists()
    finally:
        (SHARED_DIR / f"{record.id}.json").unlink(missing_ok=True)


def test_shared_schedule_supports_a_cross_year_plan() -> None:
    first = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    second = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    group_ids = [first.group_ids[0], second.group_ids[0]]
    record = store.save_shared(
        group_ids,
        subject_ids=[first.id, second.id],
        title="Repetición de 2º + 1º",
    )
    try:
        loaded = store.load_shared(record.id)
        assert set(loaded.subject_ids) == {first.id, second.id}
        assert loaded.year_id is None  # mixed years are not forced into one year
        assert loaded.title == "Repetición de 2º + 1º"
    finally:
        (SHARED_DIR / f"{record.id}.json").unlink(missing_ok=True)


def test_plan_view_can_mix_years_and_degrees() -> None:
    math = store.year_by_id["year-matematicas-1"].subject_ids[0]
    physics = store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]
    plan = store.plan_view([math, physics])
    assert {subject["id"] for subject in plan["subjects"]} == {math, physics}
    assert len(plan["years"]) == 2
    assert len(plan["degrees"]) == 2
    assert all("origin_label" in subject for subject in plan["subjects"])


def test_generate_cross_year_plan() -> None:
    math = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    physics = store.subject_by_id[
        store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]
    ]
    result = generate_schedule(store, subject_ids=[math.id, physics.id])
    assert result["status"] == "optimal"
    assert result["stats"]["conflict_count"] == 0
    assert {assignment["subject_id"] for assignment in result["assignments"]} == {
        math.id,
        physics.id,
    }
    assert {year["id"] for year in result["years"]} == {
        "year-matematicas-1",
        "year-matematicas-fisica-1",
    }


def test_plan_context_wins_over_year_inference_for_missing_groups() -> None:
    first = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    second = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    group_ids = [first.group_ids[0], first.group_ids[1]]
    result = validate_selection(store, group_ids, subject_ids=[first.id, second.id])
    assert all(item["subject_id"] in {first.id, second.id} for item in result["missing"])
    assert any(item["subject_id"] == second.id for item in result["missing"])
    # The plan does not accidentally require every subject of year 1.
    assert not any(
        item["subject_id"] == store.year_by_id["year-matematicas-1"].subject_ids[1]
        for item in result["missing"]
    )
