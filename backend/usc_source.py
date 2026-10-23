"""Access the USC timetable widget and persist a live/synthetic snapshot.

The reference app accesses the USC calendar through a Drupal AJAX POST: it sends
``_drupal_ajax=1`` plus the widget's ``ajax_page_state[libraries]`` token, finds
the ``#course-detail-controller`` HTML fragment and parses
``.calendar-day`` / ``.ml-academic-subject``.  This module reproduces that
mechanism, then stores every parsed meeting in SQLite and rebuilds the canonical
catalogue from a legacy-format CSV.

If the network or the widget fails, ``sync`` generates a small, deterministic
synthetic timetable for the configured courses instead.  That keeps the app
usable and makes the fallback explicit in ``data/usc_sync.db``.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.yaml"
FORMS_PATH = ROOT / "forms_data.json"
DB_PATH = ROOT / "data" / "usc_sync.db"
CSV_PATH = ROOT / "data" / "usc_sync.csv"
CATALOG_PATH = ROOT / "data" / "catalog.json"

# Payload copied from the reference implementation.  The token identifies the
# Drupal AJAX page state; if USC changes it, the live request falls back to the
# synthetic snapshot.
USC_PAYLOAD = {
    "js": "true",
    "_drupal_ajax": "1",
    "ajax_page_state[theme]": "usc_theme",
    "ajax_page_state[theme_token]": "",
    "ajax_page_state[libraries]": (
        "eJxljdEOwiAMRX8I5JNILQ2rDmpoMcGv183NqHtqc87NvShyZdKA73vRdsJUHR6x2phJdxMzBKgwD2NUl0XyTPEDwj9wOtSohDMoua4Y94GtdUFK7c64jE5QM3k1MPIv4zv_BvotLQoQEhVGj9Lb1msTFVpn_Pp-QexqUg5YWobKDzCW-gQuVGnO"
    ),
}
USC_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded",
}

DAYS = ("mon", "tue", "wed", "thu", "fri")
DAY_LABELS = {
    "lunes": "mon",
    "martes": "tue",
    "miércoles": "wed",
    "miercoles": "wed",
    "jueves": "thu",
    "viernes": "fri",
}
DAY_ABBR = {"mon": "L", "tue": "M", "wed": "X", "thu": "J", "fri": "V"}
TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
GROUP_RE = re.compile(r"Grupo\s*/\s*([A-Za-z0-9_]+)", re.IGNORECASE)
ROOM_RE = re.compile(r"Aula\s+(.+)$", re.IGNORECASE)
WEEK_PATH_RE = re.compile(r"/\d+/\d+(?=\?|$)")


def semester_url(url: str, week: str | None) -> str:
    """Point a widget URL at another ``<year>/<week>`` (e.g. ``26/10``)."""

    if not week:
        return url
    return WEEK_PATH_RE.sub(f"/{week.strip('/')}", url, count=1)


def week_monday(week: str) -> date:
    """Monday of a widget week path such as ``25/43``."""

    year, number = (int(part) for part in week.strip("/").split("/"))
    return date.fromisocalendar(2000 + year if year < 100 else year, number, 1)


def week_id(week: str) -> str:
    """ISO id (``2025-W43``) of a widget week path (``25/43``)."""

    iso = week_monday(week).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def week_range(first: str, last: str) -> list[str]:
    """Widget week paths from ``first`` to ``last`` inclusive, across new year."""

    current, end = week_monday(first), week_monday(last)
    if end < current:
        raise ValueError(f"Rango de semanas invertido: {first} > {last}")
    weeks = []
    while current <= end:
        iso = current.isocalendar()
        weeks.append(f"{iso.year % 100}/{iso.week}")
        current += timedelta(days=7)
    return weeks


def configured_semesters(config: dict, *, single_week: bool = False) -> list[dict]:
    """Semesters with the list of weeks to download.

    Without ``first_week``/``last_week`` (or with ``single_week``) a semester
    is downloaded as one typical week, as older configs did.
    """

    semesters = (config.get("term") or {}).get("semesters") or [{"number": 1}]
    result = []
    for item in semesters:
        week = str(item.get("week") or "") or None
        first, last = item.get("first_week"), item.get("last_week")
        if first and last and not single_week:
            weeks = week_range(str(first), str(last))
        else:
            weeks = [week]
        result.append({"number": int(item.get("number", 1)), "week": week, "weeks": weeks})
    return result


def _norm_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _norm_time(value: str) -> str:
    hour, minute = value.split(":")
    return f"{int(hour):02d}:{int(minute):02d}"


def parse_calendar_html(html: str, course_label: str) -> list[dict]:
    """Parse the USC calendar fragment into flat meeting rows.

    A row has ``course``, ``day``, ``start``, ``end``, ``subject``, ``group`` and
    ``room``.  The parser is deliberately tolerant of the two markup variants
    observed in the widget: ``Grupo/CLE_02`` and ``Grupo/CLE_02 Aula 06``.
    """

    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for day_node in soup.select(".calendar-day"):
        header = day_node.select_one(".calendar-day-header h3")
        day_name = _norm_text(header.get_text(" ", strip=True)).lower() if header else ""
        day = DAY_LABELS.get(day_name)
        if day is None:
            continue

        for subject_node in day_node.select(".ml-academic-subject"):
            title_node = subject_node.select_one("h3 a, h3")
            subject = _norm_text(title_node.get_text(" ", strip=True)) if title_node else ""
            if not subject:
                continue

            groups: list[tuple[str, str | None]] = []
            times: list[tuple[str, str]] = []
            for item in subject_node.select(".academic-subject-specs-list li"):
                text = _norm_text(item.get_text(" ", strip=True))
                time_match = TIME_RE.search(text)
                if time_match:
                    times.append(
                        (_norm_time(time_match.group(1)), _norm_time(time_match.group(2)))
                    )
                group_match = GROUP_RE.search(text)
                if group_match:
                    group = group_match.group(1).replace("_", "").upper()
                    room_match = ROOM_RE.search(text)
                    # Rooms such as "IA.01" or "LB 5" are not prefixed with
                    # "Aula": whatever follows the group code is the room.
                    rest = _norm_text(text[group_match.end():])
                    room = (
                        f"Aula {_norm_text(room_match.group(1))}"
                        if room_match
                        else rest or None
                    )
                    groups.append((group, room))

            # Some subjects are published without a group.  They cannot be
            # chosen in our model, so they are skipped rather than invented.
            if not groups or not times:
                continue

            for group, room in groups:
                for start, end in times:
                    rows.append(
                        {
                            "course": course_label,
                            "day": day,
                            "start": start,
                            "end": end,
                            "subject": subject,
                            "group": group,
                            "room": room or "",
                        }
                    )

    deduped: OrderedDict[tuple, dict] = OrderedDict()
    for row in rows:
        key = (
            row["course"],
            row["day"],
            row["start"],
            row["end"],
            row["subject"],
            row["group"],
            row["room"],
        )
        deduped[key] = row
    return list(deduped.values())


def _calendar_fragment(payload: list[dict]) -> str | None:
    for block in payload:
        data = block.get("data") or ""
        if "calendar-day" in data:
            return data
    return None


def fetch_course(
    url: str,
    course_label: str,
    timeout: float = 30.0,
    *,
    client: httpx.Client | None = None,
    allow_empty: bool = False,
) -> list[dict]:
    """Rows of one widget week.  ``allow_empty`` accepts weeks without classes."""

    response = (client or httpx).post(
        url,
        data=USC_PAYLOAD,
        headers=USC_HEADERS,
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    html = _calendar_fragment(payload)
    if html is None and allow_empty:
        # Holiday and exam weeks come back without a calendar at all.
        return []
    if html is None:
        raise ValueError("La respuesta AJAX no contenía #course-detail-controller")
    rows = parse_calendar_html(html, course_label)
    if not rows and not allow_empty:
        raise ValueError("El calendario se descargó pero no produjo sesiones")
    return rows


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def building_rules(config: dict) -> list[tuple[re.Pattern[str], str]]:
    """``(room pattern, building name)`` pairs from ``config.yaml``."""

    return [
        (re.compile(pattern), building["name"])
        for building in config.get("buildings") or []
        for pattern in building.get("rooms") or []
    ]


def building_for_room(
    room: str, default: str, rules: list[tuple[re.Pattern[str], str]]
) -> str:
    """The building of ``room``: the first matching rule, else the source's."""

    return next((name for pattern, name in rules if pattern.search(room or "")), default)


def building_short_names(config: dict) -> dict[str, str]:
    return {
        building["name"]: building["short_name"]
        for building in config.get("buildings") or []
        if building.get("short_name")
    }


SYNTHETIC_SUBJECTS = {
    "matematicas": [
        "Análisis matemático",
        "Álgebra lineal",
        "Topología",
        "Cálculo numérico",
    ],
    "fisica": ["Mecánica", "Termodinámica", "Electromagnetismo", "Óptica"],
    "ingenieria-informatica": [
        "Programación",
        "Estructuras de datos",
        "Bases de datos",
        "Sistemas operativos",
    ],
    "inteligencia-artificial": [
        "Aprendizaje automático",
        "Redes neuronales",
        "Procesamiento de lenguaje",
        "Visión artificial",
    ],
    "informatica-matematicas": [
        "Matemáticas para la informática",
        "Programación",
        "Álgebra",
        "Estructuras de datos",
    ],
    "matematicas-fisica": [
        "Análisis matemático",
        "Mecánica",
        "Álgebra lineal",
        "Termodinámica",
    ],
}


def synthetic_rows(
    course_label: str,
    forms: dict,
    degree: dict | None = None,
    year_number: int | None = None,
) -> list[dict]:
    """Deterministic invented timetable for offline/demo mode.

    Subjects are laid over distinct weekday/morning slots so a student can pick
    one group per subject without artificial overlaps.  The names and group
    codes come from the forms snapshot when available; for the new degrees they
    come from a small per-degree pool.
    """

    subjects = forms.get(course_label)
    if not subjects:
        degree_id = (degree or {}).get("id", "generic")
        pool = SYNTHETIC_SUBJECTS.get(degree_id, ["Materia 1", "Materia 2", "Materia 3"])
        suffix = f" {year_number}º" if year_number else ""
        subjects = {
            f"{name}{suffix}": ["CLE01", "CLE02", "CLIL01"]
            for name in pool
        }
    rows = []
    for index, (subject, groups) in enumerate(subjects.items()):
        day = DAYS[index % len(DAYS)]
        start_hour = 9 + (index // len(DAYS)) * 2
        if start_hour > 19:
            start_hour = 9
        start = f"{start_hour:02d}:00"
        end = f"{start_hour + 1:02d}:00"
        group_codes = [str(group).strip() for group in groups if str(group).strip()]
        if not group_codes:
            group_codes = ["CLE01"]
        for group in group_codes:
            is_lab = group.upper().startswith(("CLIL", "CLIS"))
            room = (
                f"Aula sintética {index + 1}"
                if not is_lab
                else f"Laboratorio sintético {index + 1}"
            )
            rows.append(
                {
                    "course": course_label,
                    "day": day,
                    "start": start,
                    "end": end,
                    "subject": subject,
                    "group": group.replace("_", "").upper(),
                    "room": room,
                    "building": (degree or {}).get("building"),
                }
            )
    return rows


def legacy_slot(row: dict) -> str:
    return f"{DAY_ABBR[row['day']]}{row['start'][:2]}"


def write_legacy_csv(rows: list[dict], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(
            [
                "Curso",
                "Slot",
                "Asignatura",
                "Grupo",
                "Aula",
                "Hora",
                "Edificio",
                "Cuatrimestre",
                "Semana",
            ]
        )
        # First-semester rows go first so annual subjects keep their historical ids.
        for row in sorted(rows, key=lambda item: item.get("semester", 1)):
            writer.writerow(
                [
                    row["course"],
                    legacy_slot(row),
                    row["subject"],
                    row["group"],
                    row["room"],
                    f"{row['start']}-{row['end']}",
                    row.get("building") or "",
                    row.get("semester", 1),
                    row.get("week") or "",
                ]
            )


def init_db(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT NOT NULL,
            notes TEXT,
            session_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            course TEXT NOT NULL,
            day TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            subject TEXT NOT NULL,
            group_code TEXT NOT NULL,
            room TEXT,
            source TEXT NOT NULL,
            week TEXT,
            FOREIGN KEY (run_id) REFERENCES sync_runs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_run ON sessions(run_id);
        """
    )
    # Databases created before weekly sync have no week column.
    columns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    if "week" not in columns:
        connection.execute("ALTER TABLE sessions ADD COLUMN week TEXT")
    return connection


def last_run(path: Path = DB_PATH) -> dict | None:
    if not path.exists():
        return None
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["notes"] = json.loads(result["notes"]) if result.get("notes") else []
        result["by_source"] = {
            source: count
            for source, count in connection.execute(
                "SELECT source, COUNT(*) FROM sessions WHERE run_id = ? GROUP BY source",
                (row["id"],),
            ).fetchall()
        }
        return result
    finally:
        connection.close()


def sync(
    *,
    config_path: Path = CONFIG_PATH,
    forms_path: Path = FORMS_PATH,
    db_path: Path = DB_PATH,
    csv_path: Path = CSV_PATH,
    catalog_path: Path = CATALOG_PATH,
    force_synthetic: bool = False,
    timeout: float = 30.0,
    single_week: bool = False,
    workers: int = 8,
) -> dict:
    config = load_config(config_path)
    semesters = configured_semesters(config, single_week=single_week)
    client = httpx.Client(timeout=timeout, follow_redirects=True)

    def fetch_week(url: str, week: str | None, course_label: str, weekly: bool) -> list[dict]:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                return fetch_course(
                    semester_url(url, week),
                    course_label,
                    timeout=timeout,
                    client=client,
                    allow_empty=weekly,
                )
            except Exception as error:  # noqa: BLE001 - retried, then reported
                last_error = error
        raise last_error  # type: ignore[misc]
    forms = json.loads(forms_path.read_text(encoding="utf-8"))
    degrees = config.get("degrees") or []
    if not degrees:
        raise ValueError("config.yaml no contiene la lista 'degrees'")

    started = datetime.now(timezone.utc)
    rows: list[dict] = []
    notes: list[str] = []
    course_map: dict[str, dict] = {}
    live_courses = 0
    synthetic_courses = 0

    for degree in degrees:
        degree_id = str(degree.get("id", "")).strip()
        if not degree_id:
            continue
        degree_name = degree.get("name", degree_id)
        short_name = degree.get("short_name", degree_name)
        default_building = degree.get("building") or "Sin edificio"
        campus_name = degree.get("campus") or "Campus Vida"
        for year in degree.get("years") or []:
            number = int(year.get("number"))
            year_name = year.get("name") or f"{number}º"
            course_label = f"{short_name} · {number}º"
            course_map[course_label] = {
                "degree_key": degree_id,
                "degree_id": f"deg-{degree_id}",
                "degree_name": degree_name,
                "short_name": short_name,
                "year_number": number,
                "year_name": year_name,
                "building_name": default_building,
                "campus_name": campus_name,
            }
            sources = year.get("sources") or []
            course_rows: list[dict] = []
            source_errors: list[str] = []
            later_rows: list[dict] = []
            if not force_synthetic:
                for semester in semesters:
                    semester_rows: list[dict] = []
                    semester_errors: list[str] = []
                    weekly = len(semester["weeks"]) > 1
                    for source in sources:
                        url = (source.get("url") or "").strip()
                        building = source.get("building") or default_building
                        if not url:
                            continue
                        failed_weeks: list[str] = []
                        last_error = ""
                        with ThreadPoolExecutor(max_workers=workers) as pool:
                            futures = [
                                (week, pool.submit(fetch_week, url, week, course_label, weekly))
                                for week in semester["weeks"]
                            ]
                            for week, future in futures:
                                try:
                                    fetched = future.result()
                                except Exception as error:  # noqa: BLE001 - fallback per course
                                    failed_weeks.append(week or "")
                                    last_error = f"{type(error).__name__}: {error}"
                                    continue
                                for row in fetched:
                                    row["course"] = course_label
                                    row["building"] = building
                                    row["source"] = "usc"
                                    row["semester"] = semester["number"]
                                    # A single typical week stands for every week.
                                    row["week"] = week_id(week) if weekly and week else None
                                semester_rows.extend(fetched)
                        if len(failed_weeks) == len(semester["weeks"]):
                            semester_errors.append(last_error)
                        elif failed_weeks:
                            notes.append(
                                f"{course_label}: {semester['number']}C sin las semanas "
                                f"{', '.join(failed_weeks)} ({last_error})"
                            )
                    if weekly and not semester_rows and not semester_errors:
                        semester_errors.append("ninguna semana del rango tiene clases")
                    if semester["number"] == 1:
                        course_rows, source_errors = semester_rows, semester_errors
                    elif semester_errors:
                        # Later terms are optional: without them the year simply
                        # has no second-semester subjects.
                        notes.append(
                            f"{course_label}: {semester['number']}C no disponible "
                            f"({'; '.join(semester_errors)})"
                        )
                    else:
                        later_rows.extend(semester_rows)

            if force_synthetic or source_errors or not course_rows:
                reason = "forzado" if force_synthetic else "sin fuente disponible"
                if source_errors:
                    reason = "; ".join(source_errors)
                synthetic = synthetic_rows(
                    course_label,
                    forms,
                    degree=degree,
                    year_number=number,
                )
                for row in synthetic:
                    row["course"] = course_label
                    row["building"] = default_building
                    row["source"] = "synthetic"
                course_rows = synthetic
                synthetic_courses += 1
                notes.append(f"{course_label}: sintético ({reason})")
            else:
                live_courses += 1
                course_rows.extend(later_rows)
                notes.append(f"{course_label}: {len(course_rows)} sesiones desde la USC")

            rows.extend(course_rows)

    if not rows:
        raise RuntimeError("No se pudo obtener ninguna sesión (ni real ni sintética)")

    rules = building_rules(config)
    for row in rows:
        row["building"] = building_for_room(row.get("room", ""), row.get("building") or "", rules)

    mode = (
        "live"
        if live_courses and not synthetic_courses
        else "synthetic"
        if synthetic_courses and not live_courses
        else "mixed"
    )
    write_legacy_csv(rows, csv_path)

    connection = init_db(db_path)
    try:
        cursor = connection.execute(
            "INSERT INTO sync_runs (started_at, mode, notes) VALUES (?, ?, ?)",
            (started.isoformat(), mode, json.dumps(notes, ensure_ascii=False)),
        )
        run_id = cursor.lastrowid
        connection.executemany(
            """
            INSERT INTO sessions
                (run_id, course, day, start, end, subject, group_code, room, source, week)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_id,
                    row["course"],
                    row["day"],
                    row["start"],
                    row["end"],
                    row["subject"],
                    row["group"],
                    row["room"],
                    row.get("source", "synthetic"),
                    row.get("week"),
                )
                for row in rows
            ],
        )
        connection.execute(
            "UPDATE sync_runs SET finished_at = ?, session_count = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), len(rows), run_id),
        )
        connection.commit()
    finally:
        connection.close()

    # The catalogue builder is the same one used by the CSV importer, so the
    # app-facing contract does not change when the source is live or synthetic.
    sys.path.insert(0, str(ROOT))
    from scripts.import_legacy import import_files

    synthetic_note = (
        "Datos sintéticos generados porque la fuente USC no estaba disponible."
        if synthetic_courses
        else "Datos obtenidos del widget AJAX de la USC."
    )
    catalog = import_files(
        csv_path,
        forms_path,
        catalog_path,
        extra_notes=[synthetic_note, *notes],
        course_map=course_map,
        term_info=config.get("term"),
        building_short_names=building_short_names(config),
    )

    return {
        "mode": mode,
        "course_years": len(course_map),
        "courses_live": live_courses,
        "courses_synthetic": synthetic_courses,
        "sessions": len(rows),
        "weeks": len(catalog.term.calendar),
        "csv": str(csv_path),
        "db": str(db_path),
        "catalog": str(catalog_path),
        "catalog_summary": {
            "degrees": len(catalog.degrees),
            "years": len(catalog.years),
            "subjects": len(catalog.subjects),
            "groups": len(catalog.groups),
            "sessions": len(catalog.class_sessions),
        },
        "notes": notes,
    }
