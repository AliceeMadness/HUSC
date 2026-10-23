"""Small, dependency-free time helpers shared by the API and the importer."""

from __future__ import annotations

import re
from datetime import date

DAY_CODES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_LABELS = {
    "mon": "Lunes",
    "tue": "Martes",
    "wed": "Miércoles",
    "thu": "Jueves",
    "fri": "Viernes",
    "sat": "Sábado",
    "sun": "Domingo",
}
# Legacy USC slot codes (L = lunes, M = martes, X = miércoles, J = jueves, V = viernes).
LEGACY_DAY_CODES = {
    "L": "mon",
    "M": "tue",
    "X": "wed",
    "J": "thu",
    "V": "fri",
    "S": "sat",
}

TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def parse_hm(value: str) -> int:
    """Return ``HH:MM`` as minutes since midnight, rejecting malformed values."""

    if not TIME_RE.match(value):
        raise ValueError(f"hora no válida: {value!r} (formato esperado HH:MM)")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def format_hm(minutes: int) -> str:
    if not 0 <= minutes < 24 * 60:
        raise ValueError(f"minutos fuera de rango: {minutes}")
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def duration_minutes(start: str, end: str) -> int:
    value = parse_hm(end) - parse_hm(start)
    if value <= 0:
        raise ValueError(f"la hora de fin ({end}) debe ser posterior al inicio ({start})")
    return value


def intervals_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def display_slot(day: str, start: str, end: str) -> str:
    return f"{DAY_LABELS.get(day, day)} {start}–{end}"


def academic_year_start(day: date) -> int:
    """Calendar year in which the academic year containing ``day`` began.

    Courses start in September, so August is counted as the run-up to the
    new year rather than the tail of the old one.
    """

    return day.year if day.month >= 8 else day.year - 1


def week_in_current_year(week_id: str, first_week_id: str, today: date) -> date:
    """Monday of ``week_id`` moved to the academic year that contains ``today``.

    The catalogue may describe an earlier academic year.  Each week keeps its
    ISO week number and is shifted by whole years, so week 43 of the
    downloaded year becomes week 43 of this one.
    """

    data_start = academic_year_start(_iso_monday(first_week_id))
    offset = academic_year_start(today) - data_start
    year, number = int(week_id[:4]) + offset, int(week_id[6:])
    # Not every year has an ISO week 53.
    weeks_in_year = date(year, 12, 28).isocalendar()[1]
    return date.fromisocalendar(year, min(number, weeks_in_year), 1)


def _iso_monday(week_id: str) -> date:
    return date.fromisocalendar(int(week_id[:4]), int(week_id[6:]), 1)
