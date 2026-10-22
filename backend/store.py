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
            "groups": [self.group_view(group) for group in groups],
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
            "buildings": [
                {
                    **building.model_dump(mode="json"),
                    "campus_name": (
                        self.campus_by_id[building.campus_id].name
                        if building.campus_id in self.campus_by_id
                        else None
                    ),
                }
                for building in self.catalog.buildings
            ],
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
                            **year.model_dump(mode="json"),
                            "subject_count": len(year.subject_ids),
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

    def save_shared(self, year_id: str, group_ids: list[str]) -> SharedSchedule:
        year = self.year_by_id.get(year_id)
        if year is None:
            raise KeyError(year_id)
        ordered_ids = list(dict.fromkeys(group_ids))
        unknown = [group_id for group_id in ordered_ids if group_id not in self.group_by_id]
        if unknown:
            raise ValueError(f"Grupos desconocidos: {', '.join(unknown)}")
        wrong_year = [
            group_id
            for group_id in ordered_ids
            if self.subject_by_id[self.group_by_id[group_id].subject_id].year_id != year_id
        ]
        if wrong_year:
            raise ValueError(f"Hay grupos de otro curso: {', '.join(wrong_year)}")

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

        share_id = secrets.token_hex(4)
        while (SHARED_DIR / f"{share_id}.json").exists():
            share_id = secrets.token_hex(4)

        record = SharedSchedule(
            id=share_id,
            created_at=datetime.now(timezone.utc),
            year_id=year_id,
            group_ids=ordered_ids,
            selections=selections,
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
