"""iCalendar (``.ics``) export of a timetable."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import Term
from .timeutil import week_in_current_year

WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
ICS_DAY = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}


def ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _vevent(uid: str, stamp: str, term: Term, day: date, start: str, end: str, weekly_on: str | None, **fields) -> list[str]:
    """One VEVENT; ``weekly_on`` (a day code) makes it repeat every week of the term."""

    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={term.timezone}:{day:%Y%m%d}T{start.replace(':', '')}00",
        f"DTEND;TZID={term.timezone}:{day:%Y%m%d}T{end.replace(':', '')}00",
        *([f"RRULE:FREQ=WEEKLY;COUNT={term.weeks};BYDAY={ICS_DAY[weekly_on]}"] if weekly_on else []),
        *(f"{key}:{value}" for key, value in fields.items()),
        "END:VEVENT",
    ]


def _occurrences(items: list[dict], term: Term, today: date) -> list[tuple[dict, date | None]]:
    """Each class block or activity with the date of every week it happens,
    or ``None`` when it happens every week.

    An item of only some weeks gets one dated event per week, moved to the
    current academic year; an item without weeks repeats weekly.
    """

    first_week = term.calendar[0].id if term.calendar else None
    occurrences: list[tuple[dict, date | None]] = []
    for item in items:
        if item.get("weeks") and first_week:
            occurrences.extend(
                (item, week_in_current_year(week, first_week, today) + timedelta(days=WEEKDAY_INDEX[item["day"]]))
                for week in item["weeks"]
            )
        else:
            occurrences.append((item, None))
    return occurrences


def build_calendar(validation: dict, term: Term, name: str) -> str:
    """The calendar of a validated selection: one VEVENT per merged block (not
    per hourly fragment) plus one weekly VEVENT per extracurricular activity."""

    today = date.today()
    next_monday = today + timedelta(days=((7 - today.weekday()) % 7) or 7)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Horario USC//Constructor de horarios//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{ics_escape(name)}",
        f"X-WR-TIMEZONE:{term.timezone}",
    ]

    for block, dated in _occurrences(validation["blocks"], term, today):
        event_day = dated or next_monday + timedelta(days=WEEKDAY_INDEX[block["day"]])
        location = f"{block['building_name']} · {block['room_name']}"
        description = f"{block['group_type_label']} · grupo {block['group_code']} · {block['subject_name']}. "
        if len(block.get("session_ids", [])) > 1:
            description += f"Bloque unido de {len(block['session_ids'])} sesiones. "
        description += "Generado con el constructor de horarios."
        lines.extend(
            _vevent(
                f"{block['id']}{f'-{event_day:%Y%m%d}' if dated else ''}@horario.usc",
                stamp,
                term,
                event_day,
                block["start"],
                block["end"],
                None if dated else block["day"],
                SUMMARY=f"{ics_escape(block['subject_name'])} ({block['group_code']})",
                LOCATION=ics_escape(location),
                DESCRIPTION=ics_escape(description),
            )
        )

    for event, dated in _occurrences(validation.get("custom_events", []), term, today):
        uid = ics_escape(event["id"] or "extra")
        lines.extend(
            _vevent(
                f"{uid}-{dated:%Y%m%d}@horario.usc" if dated else f"{uid}@horario.usc",
                stamp,
                term,
                dated or next_monday + timedelta(days=WEEKDAY_INDEX[event["day"]]),
                event["start"],
                event["end"],
                None if dated else event["day"],
                SUMMARY=ics_escape(event["title"]),
                LOCATION=ics_escape(event.get("location") or "Extraescolar"),
                DESCRIPTION="Actividad extraescolar." if dated else "Actividad extraescolar semanal.",
            )
        )

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
