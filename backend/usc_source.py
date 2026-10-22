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
from datetime import datetime, timezone
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
                    room = (
                        f"Aula {_norm_text(room_match.group(1))}"
                        if room_match
                        else None
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


def fetch_course(url: str, course_label: str, timeout: float = 30.0) -> list[dict]:
    response = httpx.post(
        url,
        data=USC_PAYLOAD,
        headers=USC_HEADERS,
        timeout=timeout,
        follow_redirects=True,
    )
    response.raise_for_status()
    payload = response.json()
    html = _calendar_fragment(payload)
    if html is None:
        raise ValueError("La respuesta AJAX no contenía #course-detail-controller")
    rows = parse_calendar_html(html, course_label)
    if not rows:
        raise ValueError("El calendario se descargó pero no produjo sesiones")
    return rows


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def synthetic_rows(course_label: str, forms: dict) -> list[dict]:
    """Deterministic invented timetable for offline/demo mode.

    Subjects are laid over distinct weekday/morning slots so a student can pick
    one group per subject without artificial overlaps.  The names and group
    codes come from the forms snapshot when available.
    """

    subjects = forms.get(course_label) or {
        "Asignatura inventada": ["CLE01", "CLIL01"]
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
                }
            )
    return rows


def legacy_slot(row: dict) -> str:
    return f"{DAY_ABBR[row['day']]}{row['start'][:2]}"


def write_legacy_csv(rows: list[dict], path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["Curso", "Slot", "Asignatura", "Grupo", "Aula", "Hora"])
        for row in rows:
            writer.writerow(
                [
                    row["course"],
                    legacy_slot(row),
                    row["subject"],
                    row["group"],
                    row["room"],
                    f"{row['start']}-{row['end']}",
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
            FOREIGN KEY (run_id) REFERENCES sync_runs(id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_run ON sessions(run_id);
        """
    )
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
) -> dict:
    config = load_config(config_path)
    forms = json.loads(forms_path.read_text(encoding="utf-8"))
    courses = config.get("cursos") or []
    if not courses:
        raise ValueError("config.yaml no contiene la lista 'cursos'")

    started = datetime.now(timezone.utc)
    rows: list[dict] = []
    notes: list[str] = []
    live_courses = 0
    synthetic_courses = 0

    for entry in courses:
        course_label = entry.get("curso", "").strip()
        url = entry.get("url", "").strip()
        if not course_label:
            continue
        course_rows: list[dict] = []
        if not force_synthetic and url:
            try:
                course_rows = fetch_course(url, course_label, timeout=timeout)
                if course_rows:
                    for row in course_rows:
                        row["source"] = "usc"
                    live_courses += 1
                    notes.append(f"{course_label}: {len(course_rows)} sesiones desde la USC")
            except Exception as error:  # noqa: BLE001 - any network/parse failure falls back
                notes.append(f"{course_label}: fallback sintético ({type(error).__name__}: {error})")
        if not course_rows:
            course_rows = synthetic_rows(course_label, forms)
            for row in course_rows:
                row["source"] = "synthetic"
            synthetic_courses += 1
            if force_synthetic:
                notes.append(f"{course_label}: sintético forzado ({len(course_rows)} sesiones)")
        rows.extend(course_rows)

    if not rows:
        raise RuntimeError("No se pudo obtener ninguna sesión (ni real ni sintética)")

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
                (run_id, course, day, start, end, subject, group_code, room, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    )

    return {
        "mode": mode,
        "courses_live": live_courses,
        "courses_synthetic": synthetic_courses,
        "sessions": len(rows),
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
