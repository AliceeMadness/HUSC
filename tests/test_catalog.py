"""Catalogue integrity tests: the JSON document must be self-consistent."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from backend.models import Catalog, GroupType
from backend.store import CATALOG_PATH, CatalogStore
from backend.timeutil import parse_hm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.model_validate_json(CATALOG_PATH.read_text(encoding="utf-8"))


def test_catalogue_file_exists_and_is_json(catalog: Catalog) -> None:
    assert CATALOG_PATH.exists()
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert raw["schema_version"] == "2.0"
    assert isinstance(catalog.generated_at, datetime)
    assert catalog.term.timezone == "Europe/Madrid"


def test_expected_catalogue_counts(catalog: Catalog) -> None:
    # Degree/year structure is stable; the number of meetings can change when
    # the USC snapshot is refreshed for another week.
    assert len(catalog.degrees) == 6
    assert len(catalog.years) == 27
    assert len(catalog.subjects) >= 150
    assert len(catalog.groups) >= 900
    assert len(catalog.class_sessions) >= 1400
    assert len(catalog.rooms) >= 30
    assert len(catalog.buildings) == 5


def test_new_degrees_are_present(catalog: Catalog) -> None:
    ids = {degree.id for degree in catalog.degrees}
    assert {
        "deg-matematicas",
        "deg-fisica",
        "deg-ingenieria-informatica",
        "deg-inteligencia-artificial",
        "deg-informatica-matematicas",
        "deg-matematicas-fisica",
    } <= ids
    assert {building.name for building in catalog.buildings} == {
        "Facultad de Matemáticas",
        "Facultad de Física",
        "Escuela Técnica Superior de Ingeniería",
        "Edificio Emprendia",
        "Colexio da Condesa",
    }
    # Every building carries the short label shown on each class.
    assert {building.short_name for building in catalog.buildings} == {
        "Matemáticas",
        "Física",
        "ETSE",
        "Emprendia",
        "Condesa",
    }
    rooms = {room.name for room in catalog.rooms}
    assert "Sin aula" not in rooms


def test_ids_are_url_safe_and_unique(catalog: Catalog) -> None:
    collections = [
        catalog.campuses,
        catalog.buildings,
        catalog.rooms,
        catalog.degrees,
        catalog.years,
        catalog.subjects,
        catalog.groups,
        catalog.class_sessions,
    ]
    for items in collections:
        ids = [item.id for item in items]
        assert len(ids) == len(set(ids))
        for value in ids:
            assert value.replace("-", "").isalnum(), value


def test_every_session_has_explicit_day_and_times(catalog: Catalog) -> None:
    assert {session.day.value for session in catalog.class_sessions} <= {
        "mon",
        "tue",
        "wed",
        "thu",
        "fri",
        "sat",
        "sun",
    }
    for session in catalog.class_sessions:
        assert session.start.count(":") == 1
        assert session.end.count(":") == 1
        assert parse_hm(session.start) < parse_hm(session.end)
        assert session.duration_minutes == parse_hm(session.end) - parse_hm(session.start)
        assert catalog.term.day_start <= session.start <= catalog.term.day_end
        assert catalog.term.day_start <= session.end <= catalog.term.day_end


def test_session_rooms_and_buildings_are_resolved(catalog: Catalog) -> None:
    rooms = {room.id: room for room in catalog.rooms}
    buildings = {building.id: building for building in catalog.buildings}
    for session in catalog.class_sessions:
        assert session.room_id in rooms
        assert session.building_id in buildings
        assert rooms[session.room_id].building_id == session.building_id


def test_group_types_are_derived_and_consistent(catalog: Catalog) -> None:
    sessions = {session.id: session for session in catalog.class_sessions}
    for group in catalog.groups:
        assert group.type in {
            GroupType.expositive,
            GroupType.laboratory,
            GroupType.seminar,
            GroupType.other,
        }
        for session_id in group.session_ids:
            assert sessions[session_id].group_id == group.id
            assert sessions[session_id].type == group.type


def test_legacy_slots_survive_the_migration(catalog: Catalog) -> None:
    for session in catalog.class_sessions:
        assert session.legacy is not None
        assert session.legacy.slot
        assert session.legacy.row and session.legacy.row >= 2
        assert session.legacy.source == "horario.csv"


def test_no_duplicate_group_meeting(catalog: Catalog) -> None:
    # A meeting repeated over several weeks is one session with several weeks.
    keys = [
        (session.group_id, session.day, session.start, session.end, session.room_id)
        for session in catalog.class_sessions
    ]
    assert len(keys) == len(set(keys))


def test_session_weeks_belong_to_the_calendar(catalog: Catalog) -> None:
    calendar = {week.id: week.semester for week in catalog.term.calendar}
    assert calendar, "el catálogo sincronizado semana a semana debe tener calendario"
    for session in catalog.class_sessions:
        assert session.weeks, f"{session.id} sin semanas"
        assert session.weeks == sorted(session.weeks)
        assert all(calendar[week] == session.semester for week in session.weeks)


def test_store_indexes_match_catalogue(catalog: Catalog) -> None:
    store = CatalogStore()
    assert set(store.group_by_id) == {group.id for group in catalog.groups}
    assert sum(len(sessions) for sessions in store.sessions_by_group.values()) == len(
        catalog.class_sessions
    )
    for subject in catalog.subjects:
        view = store.subject_view(subject)
        assert view["id"] == subject.id
        assert len(view["groups"]) == len(subject.group_ids)


def test_year_view_is_json_ready(catalog: Catalog) -> None:
    store = CatalogStore()
    view = store.year_view("year-matematicas-1")
    assert view["year"]["id"] == "year-matematicas-1"
    assert [s["semester"] for s in view["subjects"]] == [1] * 4 + [2] * 4
    json.dumps(view, ensure_ascii=False)  # must not raise
    first_session = view["subjects"][0]["groups"][0]["sessions"][0]
    assert {"day", "start", "end", "room_id", "building_id", "room_name"} <= first_session.keys()
