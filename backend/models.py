"""Versioned, serializable data model for the timetable catalogue.

The catalogue is intentionally a flat document: every entity has a stable string
id and references other entities by id (never by array position or by a joined
label).  That makes it deterministic to serialize to JSON, easy to diff and easy
to consume from any client.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timeutil import DAY_CODES, duration_minutes, parse_hm


class Day(str, Enum):
    mon = "mon"
    tue = "tue"
    wed = "wed"
    thu = "thu"
    fri = "fri"
    sat = "sat"
    sun = "sun"


class GroupType(str, Enum):
    expositive = "expositive"
    laboratory = "laboratory"
    seminar = "seminar"
    other = "other"


GROUP_TYPE_LABELS: dict[GroupType, str] = {
    GroupType.expositive: "Expositiva",
    GroupType.laboratory: "Laboratorio",
    GroupType.seminar: "Seminario",
    GroupType.other: "Otro",
}

GROUP_TYPE_ORDER: dict[GroupType, int] = {
    GroupType.expositive: 0,
    GroupType.laboratory: 1,
    GroupType.seminar: 2,
    GroupType.other: 3,
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceInfo(StrictModel):
    legacy_csv: str | None = None
    legacy_forms: str | None = None
    legacy_config: str | None = None
    imported_at: datetime
    source_week: int | None = None
    notes: list[str] = Field(default_factory=list)


class Term(StrictModel):
    id: str
    label: str
    academic_year: str
    timezone: str = "Europe/Madrid"
    week_start: Day = Day.mon
    day_start: str = "08:00"
    day_end: str = "21:00"
    weeks: int = 15

    @field_validator("day_start", "day_end")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        parse_hm(value)
        return value

    @model_validator(mode="after")
    def _ordered_day(self) -> "Term":
        if parse_hm(self.day_end) <= parse_hm(self.day_start):
            raise ValueError("term.day_end debe ser posterior a term.day_start")
        return self


class Campus(StrictModel):
    id: str
    name: str
    city: str | None = None


class Building(StrictModel):
    id: str
    name: str
    campus_id: str


class Room(StrictModel):
    id: str
    building_id: str
    name: str
    kind: str = "classroom"


class Degree(StrictModel):
    id: str
    name: str
    short_name: str


class AcademicYear(StrictModel):
    id: str
    degree_id: str
    number: int = Field(ge=1, le=8)
    name: str
    subject_ids: list[str] = Field(default_factory=list)


class Subject(StrictModel):
    id: str
    year_id: str
    name: str
    slug: str
    group_ids: list[str] = Field(default_factory=list)


class Group(StrictModel):
    id: str
    subject_id: str
    code: str
    type: GroupType
    type_label: str
    session_ids: list[str] = Field(default_factory=list)


class Weeks(StrictModel):
    from_week: int | None = None
    to_week: int | None = None


class LegacyRef(StrictModel):
    slot: str | None = None
    row: int | None = None
    source: str | None = None


class ClassSession(StrictModel):
    """One weekly class meeting.

    Times are stored as local wall-clock ``HH:MM`` plus an explicit day code.
    A class is never a string such as ``L09``/``09:00-10:00``: the day, start,
    end, duration and room/building references are independent fields.
    """

    id: str
    subject_id: str
    group_id: str
    type: GroupType
    day: Day
    start: str
    end: str
    duration_minutes: int = Field(gt=0)
    room_id: str
    building_id: str
    instructors: list[str] = Field(default_factory=list)
    weeks: Weeks | None = None
    note: str | None = None
    legacy: LegacyRef | None = None

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        parse_hm(value)
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "ClassSession":
        expected = duration_minutes(self.start, self.end)
        if self.duration_minutes != expected:
            raise ValueError(
                f"{self.id}: duration_minutes={self.duration_minutes} no coincide con "
                f"{self.start}-{self.end} ({expected})"
            )
        if self.day.value not in DAY_CODES:
            raise ValueError(f"{self.id}: día no válido {self.day!r}")
        return self


class SharedSelection(StrictModel):
    subject_id: str
    type: str
    group_id: str


class SharedSchedule(StrictModel):
    """Serializable snapshot of a student's choice, safe to share by URL."""

    schema_version: str = "2.0"
    id: str
    created_at: datetime
    year_id: str
    group_ids: list[str]
    selections: list[SharedSelection]


class Catalog(StrictModel):
    schema_version: str = "2.0"
    generated_at: datetime
    term: Term
    source: SourceInfo
    campuses: list[Campus] = Field(default_factory=list)
    buildings: list[Building] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    degrees: list[Degree] = Field(default_factory=list)
    years: list[AcademicYear] = Field(default_factory=list)
    subjects: list[Subject] = Field(default_factory=list)
    groups: list[Group] = Field(default_factory=list)
    class_sessions: list[ClassSession] = Field(default_factory=list)

    @model_validator(mode="after")
    def _referential_integrity(self) -> "Catalog":
        def unique_ids(name: str, items: list[object]) -> dict[str, object]:
            ids = [getattr(item, "id") for item in items]
            if len(ids) != len(set(ids)):
                duplicates = sorted({value for value in ids if ids.count(value) > 1})
                raise ValueError(f"{name}: ids duplicados {duplicates}")
            return {getattr(item, "id"): item for item in items}

        campuses = unique_ids("campuses", self.campuses)
        buildings = unique_ids("buildings", self.buildings)
        rooms = unique_ids("rooms", self.rooms)
        degrees = unique_ids("degrees", self.degrees)
        years = unique_ids("years", self.years)
        subjects = unique_ids("subjects", self.subjects)
        groups = unique_ids("groups", self.groups)
        sessions = unique_ids("class_sessions", self.class_sessions)

        for building in self.buildings:
            if building.campus_id not in campuses:
                raise ValueError(f"{building.id}: campus inexistente {building.campus_id}")
        for room in self.rooms:
            if room.building_id not in buildings:
                raise ValueError(f"{room.id}: edificio inexistente {room.building_id}")
        for year in self.years:
            if year.degree_id not in degrees:
                raise ValueError(f"{year.id}: grado inexistente {year.degree_id}")
        for subject in self.subjects:
            if subject.year_id not in years:
                raise ValueError(f"{subject.id}: curso inexistente {subject.year_id}")
        for group in self.groups:
            if group.subject_id not in subjects:
                raise ValueError(f"{group.id}: asignatura inexistente {group.subject_id}")
        for session in self.class_sessions:
            if session.subject_id not in subjects:
                raise ValueError(f"{session.id}: asignatura inexistente {session.subject_id}")
            if session.group_id not in groups:
                raise ValueError(f"{session.id}: grupo inexistente {session.group_id}")
            if session.room_id not in rooms:
                raise ValueError(f"{session.id}: aula inexistente {session.room_id}")
            if session.building_id not in buildings:
                raise ValueError(f"{session.id}: edificio inexistente {session.building_id}")
            if rooms[session.room_id].building_id != session.building_id:
                raise ValueError(f"{session.id}: el aula no pertenece al edificio indicado")

        for year in self.years:
            if set(year.subject_ids) != {
                subject.id for subject in self.subjects if subject.year_id == year.id
            }:
                raise ValueError(f"{year.id}: subject_ids no coincide con las asignaturas")
        for subject in self.subjects:
            if set(subject.group_ids) != {
                group.id for group in self.groups if group.subject_id == subject.id
            }:
                raise ValueError(f"{subject.id}: group_ids no coincide con los grupos")
        for group in self.groups:
            if set(group.session_ids) != {
                session.id for session in self.class_sessions if session.group_id == group.id
            }:
                raise ValueError(f"{group.id}: session_ids no coincide con las sesiones")
            for session_id in group.session_ids:
                session = sessions[session_id]
                if session.type != group.type:
                    raise ValueError(f"{group.id}: la sesión {session_id} tiene otro tipo")
        return self
