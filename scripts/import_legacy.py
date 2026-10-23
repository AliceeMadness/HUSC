"""Import the legacy USC CSV/JSON snapshots into the canonical catalogue.

Run from the repository root:

    python scripts/import_legacy.py

The legacy files stay untouched.  The output is ``data/catalog.json``, a
versioned document with stable ids and fully explicit day/start/end/room/
building fields.  The importer is deliberately conservative: every assumption
(room building, group type) is documented in ``source.notes`` and printed as a
warning when the source data is ambiguous.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.ids import natural_key, slugify  # noqa: E402
from backend.models import (  # noqa: E402
    GROUP_TYPE_LABELS,
    GROUP_TYPE_ORDER,
    AcademicYear,
    Building,
    Campus,
    Catalog,
    ClassSession,
    Degree,
    Group,
    GroupType,
    LegacyRef,
    Room,
    SourceInfo,
    Subject,
    TeachingWeek,
    Term,
)
from backend.timeutil import (  # noqa: E402
    DAY_LABELS,
    LEGACY_DAY_CODES,
    duration_minutes,
    parse_hm,
)

CSV_PATH = ROOT / "horario.csv"
FORMS_PATH = ROOT / "forms_data.json"
OUTPUT_PATH = ROOT / "data" / "catalog.json"

ORDINALS = {
    "primero": 1,
    "segundo": 2,
    "tercero": 3,
    "cuarto": 4,
    "quinto": 5,
    "sexto": 6,
}

DEGREES = {
    "matematicas": {
        "id": "deg-matematicas",
        "name": "Grado en Matemáticas",
        "short_name": "Matemáticas",
        "building_id": "bld-facultad-de-matematicas",
        "building_name": "Facultad de Matemáticas",
    },
    "matematicas-fisica": {
        "id": "deg-matematicas-fisica",
        "name": "Doble Grado en Matemáticas y Física",
        "short_name": "Matemáticas + Física",
        "building_id": "bld-facultad-de-fisica",
        "building_name": "Facultad de Física",
    },
}

CAMPUS = {
    "id": "campus-vida",
    "name": "Campus Vida",
    "city": "Santiago de Compostela",
}

DAY_ORDER = {day: index for index, day in enumerate(DAY_LABELS)}


def parse_course_label(label: str) -> tuple[str, int, str]:
    """``Primero Mates-Física`` -> (degree key, year number, display name)."""

    clean = " ".join(label.split())
    lowered = clean.lower()
    if lowered.endswith("mates-física") or lowered.endswith("mates-fisica"):
        base = clean[: -(len("Mates-Física"))].strip()
        degree_key = "matematicas-fisica"
    else:
        base = clean
        degree_key = "matematicas"
    number = ORDINALS.get(base.lower())
    if number is None:
        raise ValueError(f"Curso desconocido en el CSV: {label!r}")
    return degree_key, number, base


def group_type_from_code(code: str) -> GroupType:
    upper = code.upper()
    if upper.startswith("CLE"):
        return GroupType.expositive
    if upper.startswith("CLIL"):
        return GroupType.laboratory
    if upper.startswith("CLIS"):
        return GroupType.seminar
    return GroupType.other


def parse_legacy_slot(slot: str, start: str) -> tuple[str, str | None]:
    """Convert ``L09`` to ``("mon", None)``.

    The CSV's slot only encodes day + hour, while the ``Hora`` column has the
    real interval.  A warning is returned when the hour disagrees (some
    Mates-Física classes start at half past the hour).
    """

    clean = slot.strip().upper()
    if len(clean) < 2 or clean[0] not in LEGACY_DAY_CODES:
        raise ValueError(f"slot no reconocido: {slot!r}")
    day = LEGACY_DAY_CODES[clean[0]]
    warning = None
    slot_hour = clean[1:]
    if slot_hour.isdigit() and int(slot_hour) != parse_hm(start) // 60:
        warning = (
            f"el slot {slot} empieza a las {start}; se usa la hora real de la columna Hora"
        )
    return day, warning


def room_kind(name: str) -> str:
    lowered = name.lower()
    if name == "Sin aula":
        return "other"
    if "informática" in lowered or "informatica" in lowered:
        return "computer_lab"
    if "laboratorio" in lowered:
        return "laboratory"
    return "classroom"


def session_id(group_id: str, day: str, start: str, room_id: str, end: str | None = None) -> str:
    # ``end`` only takes part when two meetings share group, day, start and
    # room but end at different times (in different weeks); older ids stay.
    raw = f"{group_id}|{day}|{start}|{room_id}" + (f"|{end}" if end else "")
    return f"ses-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:10]}"


WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def import_files(
    csv_path: Path = CSV_PATH,
    forms_path: Path = FORMS_PATH,
    output_path: Path = OUTPUT_PATH,
    extra_notes: list[str] | None = None,
    course_map: dict[str, dict] | None = None,
    term_info: dict | None = None,
    building_short_names: dict[str, str] | None = None,
) -> Catalog:
    """Build the catalogue from a normalized legacy CSV.

    ``course_map`` is used by the USC sync: it maps a CSV ``Curso`` label to
    explicit degree/year metadata and lets a double degree have multiple source
    URLs (and therefore different buildings) for the same year.
    """

    if not csv_path.exists() or not forms_path.exists():
        raise SystemExit(f"Faltan {csv_path} o {forms_path}")

    forms = json.loads(forms_path.read_text(encoding="utf-8"))
    warnings: list[str] = []

    # Ordered maps keep the generated document deterministic and pleasant to read.
    campuses: "OrderedDict[str, Campus]" = OrderedDict()
    campuses[CAMPUS["id"]] = Campus(**CAMPUS)
    degrees: "OrderedDict[str, Degree]" = OrderedDict()
    years: "OrderedDict[str, AcademicYear]" = OrderedDict()
    subjects: "OrderedDict[str, Subject]" = OrderedDict()
    groups: "OrderedDict[str, Group]" = OrderedDict()
    rooms: "OrderedDict[str, Room]" = OrderedDict()
    buildings: "OrderedDict[str, Building]" = OrderedDict()
    sessions: "OrderedDict[str, ClassSession]" = OrderedDict()
    seen_sessions: dict[tuple[str, str, str, str, str], str] = {}
    # Weeks each meeting was seen in; a row without week means "every week".
    session_weeks: dict[str, set[str] | None] = {}
    calendar: dict[str, int] = {}

    subject_order: dict[str, list[str]] = {}
    forms_subject_groups: dict[tuple[str, str], list[str]] = {}

    for course_label, subject_map in forms.items():
        degree_key, year_number, _ = parse_course_label(course_label)
        year_id = f"year-{degree_key}-{year_number}"
        subject_order[year_id] = list(subject_map.keys())
        for subject_name, group_codes in subject_map.items():
            forms_subject_groups[(year_id, subject_name.strip())] = list(group_codes)

    csv_rows = 0
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        required = {"Curso", "Slot", "Asignatura", "Grupo", "Aula", "Hora"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise SystemExit(f"El CSV no tiene las columnas esperadas: {sorted(missing)}")

        for row_number, row in enumerate(reader, start=2):
            csv_rows += 1
            course_label = (row.get("Curso") or "").strip()
            subject_name = " ".join((row.get("Asignatura") or "").split())
            group_code = (row.get("Grupo") or "").strip()
            room_name = " ".join((row.get("Aula") or "").split())
            slot = (row.get("Slot") or "").strip()
            hora = (row.get("Hora") or "").strip()
            if not all([course_label, subject_name, group_code, slot, hora]):
                warnings.append(f"fila {row_number}: incompleta, se omite")
                continue
            if not room_name:
                room_name = "Sin aula"
            semester_raw = (row.get("Cuatrimestre") or "1").strip()
            semester = int(semester_raw) if semester_raw in {"1", "2"} else 1
            week = (row.get("Semana") or "").strip() or None
            if week and not WEEK_RE.match(week):
                warnings.append(f"fila {row_number}: semana no válida {week!r}, se ignora")
                week = None
            if week:
                if calendar.setdefault(week, semester) != semester:
                    warnings.append(f"fila {row_number}: la semana {week} está en dos cuatrimestres")

            meta = course_map.get(course_label) if course_map else None
            if meta:
                degree_key = meta["degree_key"]
                degree_id = meta.get("degree_id", f"deg-{degree_key}")
                degree_name = meta["degree_name"]
                short_name = meta["short_name"]
                year_number = int(meta["year_number"])
                year_name = meta["year_name"]
                default_building_name = meta.get("building_name", "Sin edificio")
                campus_name = meta.get("campus_name", CAMPUS["name"])
            else:
                degree_key, year_number, year_name = parse_course_label(course_label)
                degree_def = DEGREES[degree_key]
                degree_id = degree_def["id"]
                degree_name = degree_def["name"]
                short_name = degree_def["short_name"]
                default_building_name = degree_def["building_name"]
                campus_name = CAMPUS["name"]

            year_id = f"year-{degree_key}-{year_number}"
            if degree_id not in degrees:
                degrees[degree_id] = Degree(
                    id=degree_id,
                    name=degree_name,
                    short_name=short_name,
                )
            if year_id not in years:
                years[year_id] = AcademicYear(
                    id=year_id,
                    degree_id=degree_id,
                    number=year_number,
                    name=year_name,
                )

            start, _, end = hora.partition("-")
            start, end = start.strip(), end.strip()
            day, slot_warning = parse_legacy_slot(slot, start)
            if slot_warning:
                warnings.append(f"fila {row_number}: {slot_warning}")

            row_building = " ".join((row.get("Edificio") or "").split())
            building_name = row_building or default_building_name
            building_id = f"bld-{slugify(building_name)}"
            campus_id = f"campus-{slugify(campus_name)}"
            if campus_id not in campuses:
                campuses[campus_id] = Campus(id=campus_id, name=campus_name)
            if building_id not in buildings:
                buildings[building_id] = Building(
                    id=building_id,
                    name=building_name,
                    campus_id=campus_id,
                    short_name=(building_short_names or {}).get(building_name),
                )

            room_id = f"room-{building_id.removeprefix('bld-')}-{slugify(room_name)}"
            if room_id not in rooms:
                rooms[room_id] = Room(
                    id=room_id,
                    building_id=building_id,
                    name=room_name,
                    kind=room_kind(room_name),
                )

            subject_id = f"sub-{year_id.removeprefix('year-')}-{slugify(subject_name)}"
            # An annual subject is taught in both terms; each term gets its own
            # subject so its groups and sessions never mix.  Rows are written
            # first-semester first, so the plain id keeps its historical meaning.
            if subject_id in subjects and subjects[subject_id].semester != semester:
                subject_id = f"{subject_id}-c{semester}"
            if subject_id not in subjects:
                subjects[subject_id] = Subject(
                    id=subject_id,
                    year_id=year_id,
                    name=subject_name,
                    slug=slugify(subject_name),
                    semester=semester,
                )

            group_id = f"grp-{subject_id.removeprefix('sub-')}-{group_code.lower()}"
            if group_id not in groups:
                group_type = group_type_from_code(group_code)
                groups[group_id] = Group(
                    id=group_id,
                    subject_id=subject_id,
                    code=group_code,
                    type=group_type,
                    type_label=GROUP_TYPE_LABELS[group_type],
                )

            group_type = groups[group_id].type
            key = (group_id, day, start, end, room_id)
            if key in seen_sessions and week:
                # The same meeting in another week: remember the week.
                known = session_weeks[seen_sessions[key]]
                if known is not None:
                    known.add(week)
                continue
            if key in seen_sessions:
                warnings.append(
                    f"fila {row_number}: sesión duplicada de {group_code} "
                    f"({DAY_LABELS[day]} {start}) ya presente en la fila "
                    f"{sessions[seen_sessions[key]].legacy.row}"
                )
                continue

            new_id = session_id(group_id, day, start, room_id)
            if new_id in sessions:
                new_id = session_id(group_id, day, start, room_id, end)
            session = ClassSession(
                id=new_id,
                subject_id=subject_id,
                group_id=group_id,
                type=group_type,
                day=day,
                start=start,
                end=end,
                duration_minutes=duration_minutes(start, end),
                room_id=room_id,
                building_id=building_id,
                semester=subjects[subject_id].semester,
                weeks=None,
                legacy=LegacyRef(slot=slot, row=row_number, source="horario.csv"),
            )
            sessions[session.id] = session
            seen_sessions[key] = session.id
            session_weeks[session.id] = {week} if week else None

    week_order = sorted(calendar)
    for sid, seen_weeks in session_weeks.items():
        if seen_weeks is not None:
            sessions[sid].weeks = [item for item in week_order if item in seen_weeks]

    # ---- Cross-check forms_data.json against the CSV -----------------------
    # The USC sync supplies its own explicit course map and may include degrees
    # that were never in the old forms snapshot; skip the legacy warnings there.
    for year_id, ordered_names in ({} if course_map else subject_order).items():
        for name in ordered_names:
            subject_id = f"sub-{year_id.removeprefix('year-')}-{slugify(name)}"
            if subject_id not in subjects:
                warnings.append(
                    f"{name!r} aparece en forms_data.json ({year_id}) pero no tiene "
                    "sesiones en horario.csv; se omite del catálogo"
                )
                continue
            expected = {
                code.strip().lower()
                for code in forms_subject_groups.get((year_id, name), [])
                if code.strip()
            }
            actual = {
                group.code.lower()
                for group in groups.values()
                if group.subject_id == subject_id
            }
            for code in sorted(expected - actual, key=natural_key):
                warnings.append(
                    f"{name!r}: el grupo {code.upper()} está en forms_data.json pero "
                    "no tiene sesiones en horario.csv; no se incluye"
                )

    # ---- Stable ordering and back-references ------------------------------
    degree_order = {degree_id: index for index, degree_id in enumerate(degrees)}
    year_order = {
        year_id: (degree_order[year.degree_id], year.number) for year_id, year in years.items()
    }

    def subject_rank(subject: Subject) -> tuple[object, ...]:
        ordered = subject_order.get(subject.year_id, [])
        try:
            position = ordered.index(subject.name)
        except ValueError:
            position = len(ordered)
        return (year_order[subject.year_id], subject.semester, position, subject.name)

    ordered_subjects = sorted(subjects.values(), key=subject_rank)
    for subject in ordered_subjects:
        subject_groups = sorted(
            (group for group in groups.values() if group.subject_id == subject.id),
            key=lambda group: (GROUP_TYPE_ORDER[group.type], natural_key(group.code)),
        )
        subject.group_ids = [group.id for group in subject_groups]
        years[subject.year_id].subject_ids.append(subject.id)

    for group in groups.values():
        group_sessions = sorted(
            (session for session in sessions.values() if session.group_id == group.id),
            key=lambda session: (DAY_ORDER[session.day], session.start),
        )
        group.session_ids = [session.id for session in group_sessions]

    years_sorted = sorted(years.values(), key=lambda year: (degree_order[year.degree_id], year.number))
    subjects_sorted = ordered_subjects
    groups_sorted = sorted(
        groups.values(),
        key=lambda group: (
            year_order[subjects[group.subject_id].year_id],
            subject_rank(subjects[group.subject_id]),
            GROUP_TYPE_ORDER[group.type],
            natural_key(group.code),
        ),
    )
    sessions_sorted = sorted(
        sessions.values(),
        key=lambda session: (
            year_order[subjects[session.subject_id].year_id],
            subject_rank(subjects[session.subject_id]),
            GROUP_TYPE_ORDER[session.type],
            natural_key(group_code := groups[session.group_id].code),
            DAY_ORDER[session.day],
            session.start,
            group_code,
        ),
    )

    # Internal overlaps within one group are always a data problem.
    by_group: dict[str, list[ClassSession]] = defaultdict(list)
    for session in sessions_sorted:
        by_group[session.group_id].append(session)
    for group_id, group_sessions in by_group.items():
        for index, first in enumerate(group_sessions):
            for second in group_sessions[index + 1 :]:
                if first.day != second.day:
                    continue
                if first.weeks is not None and second.weeks is not None and not (
                    set(first.weeks) & set(second.weeks)
                ):
                    continue
                if parse_hm(first.start) < parse_hm(second.end) and parse_hm(second.start) < parse_hm(first.end):
                    warnings.append(
                        f"solapamiento interno en {group_id}: "
                        f"{first.start}-{first.end} y {second.start}-{second.end} "
                        f"({DAY_LABELS[first.day]})"
                    )

    term_meta = term_info or {}
    source_week = int(term_meta.get("source_week", 7))
    notes = []
    if course_map:
        notes.append(
            "Grados, cursos y edificios provienen de config.yaml. Un doble grado puede "
            "tener varias fuentes por curso; cada sesión conserva el edificio de su fuente."
        )
    else:
        notes.append(
            "El CSV de origen no incluye edificio ni tipo de sesión: el edificio se infiere "
            "del grado y el tipo, del prefijo del grupo (CLE, CLIL, CLIS)."
        )
    if calendar:
        week_note = (
            f"Se han descargado {len(calendar)} semanas lectivas. Cada sesión indica en "
            "weeks las semanas ISO en que se imparte; term.calendar las lista."
        )
    else:
        week_note = (
            f"Los datos representan una semana tipo (semana {source_week} en las URLs de "
            "config.yaml). No se codifican semanas concretas: weeks queda a null."
        )
    notes.extend(
        [
            week_note,
            "Las horas L09/M09/... se han descompuesto en day (mon..fri) y start/end HH:MM. "
            "El slot original se conserva en legacy.slot.",
        ]
    )
    if extra_notes:
        notes.extend(extra_notes)

    catalog = Catalog(
        schema_version="2.0",
        generated_at=datetime.now(timezone.utc),
        term=Term(
            id=term_meta.get("id", "usc-2026-27-semana-tipo"),
            label=term_meta.get("label", "Curso 2026/27 · semana tipo"),
            academic_year=term_meta.get("academic_year", "2026/27"),
            timezone=term_meta.get("timezone", "Europe/Madrid"),
            week_start=term_meta.get("week_start", "mon"),
            day_start=term_meta.get("day_start", "08:00"),
            day_end=term_meta.get("day_end", "21:00"),
            weeks=int(term_meta.get("weeks", 15)),
            calendar=[
                TeachingWeek(
                    id=week,
                    semester=calendar[week],
                    monday=date.fromisocalendar(int(week[:4]), int(week[6:]), 1),
                )
                for week in week_order
            ],
        ),
        source=SourceInfo(
            legacy_csv=str(csv_path.resolve().relative_to(ROOT)).replace("\\", "/")
            if csv_path.resolve().is_relative_to(ROOT)
            else str(csv_path.resolve()),
            legacy_forms=str(forms_path.resolve().relative_to(ROOT)).replace("\\", "/")
            if forms_path.resolve().is_relative_to(ROOT)
            else str(forms_path.resolve()),
            legacy_config="config.yaml",
            imported_at=datetime.now(timezone.utc),
            source_week=source_week,
            notes=notes,
        ),
        campuses=list(campuses.values()),
        buildings=list(buildings.values()),
        rooms=sorted(rooms.values(), key=lambda room: (room.building_id, natural_key(room.name))),
        degrees=list(degrees.values()),
        years=years_sorted,
        subjects=subjects_sorted,
        groups=groups_sorted,
        class_sessions=sessions_sorted,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(catalog.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ROOT / "data" / "shared_schedules").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "shared_schedules" / ".gitkeep").touch()

    print(f"Leídas {csv_rows} filas de {csv_path}")
    print(f"Catálogo escrito en {output_path}")
    print(
        f"Resumen: {len(degrees)} grados · {len(years_sorted)} cursos · "
        f"{len(subjects_sorted)} asignaturas · {len(groups_sorted)} grupos · "
        f"{len(sessions_sorted)} sesiones · {len(rooms)} aulas · {len(buildings)} edificios"
    )
    if warnings:
        print(f"\n{len(warnings)} avisos:")
        for warning in warnings:
            print(f"  - {warning}")
    else:
        print("Sin avisos de integridad.")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV_PATH, help="CSV legacy de entrada")
    parser.add_argument("--forms", type=Path, default=FORMS_PATH, help="JSON de grupos")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH, help="catálogo JSON de salida")
    parser.add_argument("--note", action="append", default=[], help="nota adicional de la fuente")
    args = parser.parse_args()
    import_files(args.csv, args.forms, args.output, extra_notes=args.note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
