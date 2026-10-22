"""Small, dependency-free time helpers shared by the API and the importer."""

from __future__ import annotations

import re

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
