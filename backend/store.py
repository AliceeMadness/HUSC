"""Catalogue loading, enriched views and persistence of shared schedules."""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    AcademicYear,
    Building,
    Catalog,
    Campus,
    ClassSession,
    Degree,
    Group,
    Room,
    CustomEvent,
    SharedSchedule,
    SharedSelection,
    Subject,
)
from .timeutil import DAY_CODES, DAY_LABELS

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "catalog.json"
SHARED_DIR = ROOT / "data" / "shared_schedules"
SHARE_ID_RE = re.compile(r"^[a-f0-9]{8}$")


class CatalogStore:
    """Read-only, in-memory index over the canonical JSON catalogue."""

    def __init__(self, path: Path = CATALOG_PATH) -> None:
        self.path = path
        self.reload()

    def reload(self) -> None:
        if not self.path.exists():
            raise FileNotFoundError(
                f"No existe {self.path}. Ejecuta `python scripts/import_legacy.py` primero."
            )
        self.catalog = Catalog.model_validate_json(self.path.read_text(encoding="utf-8"))
        self.campus_by_id: dict[str, Campus] = {item.id: item for item in self.catalog.campuses}
        self.building_by_id: dict[str, Building] = {
            item.id: item for item in self.catalog.buildings
        }
        self.room_by_id: dict[str, Room] = {item.id: item for item in self.catalog.rooms}
        self.degree_by_id: dict[str, Degree] = {item.id: item for item in self.catalog.degrees}
        self.year_by_id: dict[str, AcademicYear] = {item.id: item for item in self.catalog.years}
        self.subject_by_id: dict[str, Subject] = {
            item.id: item for item in self.catalog.subjects
        }
        self.group_by_id: dict[str, Group] = {item.id: item for item in self.catalog.groups}
        self.session_by_id: dict[str, ClassSession] = {
            item.id: item for item in self.catalog.class_sessions
        }
        self.sessions_by_group: dict[str, list[ClassSession]] = {
            group_id: [] for group_id in self.group_by_id
        }
        for session in self.catalog.class_sessions:
            self.sessions_by_group[session.group_id].append(session)
        day_order = {day: index for index, day in enumerate(DAY_CODES)}
        for sessions in self.sessions_by_group.values():
            sessions.sort(key=lambda session: (day_order[session.day.value], session.start))

    # ---- serialization helpers -------------------------------------------

    def session_view(self, session: ClassSession) -> dict:
        room = self.room_by_id[session.room_id]
        building = self.building_by_id[session.building_id]
        subject = self.subject_by_id[session.subject_id]
        group = self.group_by_id[session.group_id]
        campus = self.campus_by_id.get(building.campus_id)
        return {
            "id": session.id,
            "subject_id": session.subject_id,
            "subject_name": subject.name,
            "subject_slug": subject.slug,
            "group_id": session.group_id,
            "group_code": group.code,
            "group_type": group.type.value,
            "group_type_label": group.type_label,
            "day": session.day.value,
            "day_label": DAY_LABELS[session.day.value],
            "semester": session.semester,
            "start": session.start,
            "end": session.end,
            "duration_minutes": session.duration_minutes,
            "room_id": session.room_id,
            "room_name": room.name,
            "room_kind": room.kind,
            "building_id": session.building_id,
            "building_name": building.name,
            "campus_name": campus.name if campus else None,
            "instructors": list(session.instructors),
            "weeks": session.weeks.model_dump(mode="json") if session.weeks else None,
            "note": session.note,
            "legacy_slot": session.legacy.slot if session.legacy else None,
        }

    def group_view(self, group: Group) -> dict:
        sessions = self.sessions_by_group[group.id]
        return {
            "id": group.id,
            "subject_id": group.subject_id,
            "code": group.code,
            "type": group.type.value,
            "type_label": group.type_label,
            "session_count": len(sessions),
            "weekly_minutes": sum(session.duration_minutes for session in sessions),
            "sessions": [self.session_view(session) for session in sessions],
        }

    def subject_view(self, subject: Subject) -> dict:
        groups = [self.group_by_id[group_id] for group_id in subject.group_ids]
        return {
            "id": subject.id,
            "year_id": subject.year_id,
            "name": subject.name,
            "slug": subject.slug,
            "semester": subject.semester,
            "groups": [self.group_view(group) for group in groups],
        }

    def buildings_view(self) -> list[dict]:
        return [
            {
                **building.model_dump(mode="json"),
                "campus_name": (
                    self.campus_by_id[building.campus_id].name
                    if building.campus_id in self.campus_by_id
                    else None
                ),
            }
            for building in self.catalog.buildings
        ]

    def year_summary(self, year: AcademicYear) -> dict:
        degree = self.degree_by_id[year.degree_id]
        return {
            "id": year.id,
            "degree_id": year.degree_id,
            "degree_name": degree.name,
            "degree_short_name": degree.short_name,
            "number": year.number,
            "name": year.name,
            "subject_count": len(year.subject_ids),
        }

    def year_view(self, year_id: str) -> dict:
        year = self.year_by_id.get(year_id)
        if year is None:
            raise KeyError(year_id)
        degree = self.degree_by_id[year.degree_id]
        subjects = [self.subject_view(self.subject_by_id[subject_id]) for subject_id in year.subject_ids]
        return {
            "term": self.catalog.term.model_dump(mode="json"),
            "degree": degree.model_dump(mode="json"),
            "year": year.model_dump(mode="json"),
            "subjects": subjects,
            "buildings": self.buildings_view(),
        }

    def plan_view(self, subject_ids: list[str]) -> dict:
        """A student's plan: any set of subjects from any year or degree.

        The response is ordered by catalogue order (degree, year, subject), not
        by the order the client sent the ids, so the UI is deterministic.
        """

        ordered_ids = list(dict.fromkeys(subject_ids))
        if not ordered_ids:
            raise ValueError("La matrícula no puede estar vacía")
        unknown = [subject_id for subject_id in ordered_ids if subject_id not in self.subject_by_id]
        if unknown:
            raise ValueError(f"Asignaturas desconocidas: {', '.join(unknown)}")

        catalogue_order = {subject.id: index for index, subject in enumerate(self.catalog.subjects)}
        ordered_ids.sort(key=lambda subject_id: catalogue_order[subject_id])

        subjects = []
        year_ids: list[str] = []
        degree_ids: list[str] = []
        for subject_id in ordered_ids:
            subject = self.subject_by_id[subject_id]
            year = self.year_by_id[subject.year_id]
            degree = self.degree_by_id[year.degree_id]
            view = self.subject_view(subject)
            view["year"] = self.year_summary(year)
            view["degree"] = degree.model_dump(mode="json")
            view["origin_label"] = f"{degree.short_name} · {year.number}º"
            subjects.append(view)
            if year.id not in year_ids:
                year_ids.append(year.id)
            if degree.id not in degree_ids:
                degree_ids.append(degree.id)

        return {
            "term": self.catalog.term.model_dump(mode="json"),
            "subject_ids": ordered_ids,
            "subjects": subjects,
            "years": [self.year_summary(self.year_by_id[year_id]) for year_id in year_ids],
            "degrees": [
                self.degree_by_id[degree_id].model_dump(mode="json") for degree_id in degree_ids
            ],
            "buildings": self.buildings_view(),
        }

    def degree_summaries(self) -> list[dict]:
        result = []
        for degree in self.catalog.degrees:
            years = [year for year in self.catalog.years if year.degree_id == degree.id]
            result.append(
                {
                    **degree.model_dump(mode="json"),
                    "years": [
                        {
                            **self.year_summary(year),
                            "subjects": [
                                {
                                    "id": subject.id,
                                    "name": subject.name,
                                    "slug": subject.slug,
                                    "semester": subject.semester,
                                    "group_count": len(subject.group_ids),
                                    "session_count": sum(
                                        len(self.sessions_by_group[group_id])
                                        for group_id in subject.group_ids
                                    ),
                                }
                                for subject_id in year.subject_ids
                                for subject in [self.subject_by_id[subject_id]]
                            ],
                            "session_count": sum(
                                len(self.sessions_by_group[group_id])
                                for subject_id in year.subject_ids
                                for group_id in self.subject_by_id[subject_id].group_ids
                            ),
                        }
                        for year in sorted(years, key=lambda item: item.number)
                    ],
                }
            )
        return result

    def full_export(self) -> dict:
        return self.catalog.model_dump(mode="json")

    # ---- shared schedules -------------------------------------------------

    def save_shared(
        self,
        group_ids: list[str],
        subject_ids: list[str] | None = None,
        title: str | None = None,
        year_id: str | None = None,
        custom_events: list[dict] | None = None,
        pinned_group_ids: list[str] | None = None,
    ) -> SharedSchedule:
        """Store a snapshot of a plan, possibly spanning several years/degrees.

        ``subject_ids`` defines the plan even if the student has not chosen a
        group for every subject yet.  When omitted (old clients), it is derived
        from the chosen groups.
        """

        if year_id is not None and year_id not in self.year_by_id:
            raise KeyError(year_id)

        ordered_ids = list(dict.fromkeys(group_ids))
        unknown = [group_id for group_id in ordered_ids if group_id not in self.group_by_id]
        if unknown:
            raise ValueError(f"Grupos desconocidos: {', '.join(unknown)}")

        group_subject_ids = list(
            dict.fromkeys(self.group_by_id[group_id].subject_id for group_id in ordered_ids)
        )
        plan_ids = (
            list(dict.fromkeys(subject_ids)) if subject_ids is not None else group_subject_ids
        )
        if not plan_ids:
            raise ValueError("La matrícula no puede estar vacía")
        unknown_subjects = [
            subject_id for subject_id in plan_ids if subject_id not in self.subject_by_id
        ]
        if unknown_subjects:
            raise ValueError(f"Asignaturas desconocidas: {', '.join(unknown_subjects)}")
        outside_plan = [
            subject_id for subject_id in group_subject_ids if subject_id not in plan_ids
        ]
        if outside_plan:
            raise ValueError(
                "Hay grupos de asignaturas que no están en la matrícula: "
                + ", ".join(outside_plan)
            )

        selections = []
        for group_id in ordered_ids:
            group = self.group_by_id[group_id]
            selections.append(
                SharedSelection(
                    subject_id=group.subject_id,
                    type=group.type.value,
                    group_id=group.id,
                )
            )

        pins = list(dict.fromkeys(pinned_group_ids or []))
        unknown_pins = [group_id for group_id in pins if group_id not in ordered_ids]
        if unknown_pins:
            raise ValueError(
                "Hay grupos fijados que no están seleccionados: " + ", ".join(unknown_pins)
            )
        events = [CustomEvent.model_validate(event) for event in custom_events or []]

        share_id = secrets.token_hex(4)
        while (SHARED_DIR / f"{share_id}.json").exists():
            share_id = secrets.token_hex(4)

        inferred_years = {
            self.subject_by_id[subject_id].year_id for subject_id in plan_ids
        }
        # ``year_id`` is only a convenience for single-year plans; a mixed
        # matrícula must not pretend to belong to one year.
        year_id = next(iter(inferred_years)) if len(inferred_years) == 1 else None

        record = SharedSchedule(
            id=share_id,
            created_at=datetime.now(timezone.utc),
            year_id=year_id,
            subject_ids=plan_ids,
            title=title,
            group_ids=ordered_ids,
            selections=selections,
            custom_events=events,
            pinned_group_ids=pins,
        )
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        target = SHARED_DIR / f"{share_id}.json"
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return record

    def load_shared(self, share_id: str) -> SharedSchedule:
        if not SHARE_ID_RE.match(share_id):
            raise KeyError(share_id)
        path = SHARED_DIR / f"{share_id}.json"
        if not path.exists():
            raise KeyError(share_id)
        return SharedSchedule.model_validate_json(path.read_text(encoding="utf-8"))


store = CatalogStore()
