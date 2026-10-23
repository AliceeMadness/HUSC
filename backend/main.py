"""FastAPI application: JSON API + static single-page frontend."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from . import __version__
from .ids import slugify
from .models import Catalog, CustomEvent, Day
from .scheduling import (
    free_slots_for_groups,
    generate_schedule,
    optimize_schedule,
    resolve_groups,
    validate_selection,
)
from .store import ROOT, store
from .timeutil import DAY_LABELS, parse_hm, week_in_current_year

FRONTEND_DIR = ROOT / "frontend"
WEEKDAY_INDEX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
ICS_DAY = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}

app = FastAPI(
    title="Horario USC API",
    version=__version__,
    description=(
        "Constructor de horarios para los grados de Matemáticas y Matemáticas + Física "
        "de la USC. Catálogo versionado, validación de solapes, huecos libres y "
        "generación automática de combinaciones de grupos."
    ),
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def revalidate_frontend(request, call_next):
    """Make browsers revalidate HTML/CSS/JS so edits show up on a normal reload."""

    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class BlockedSlot(BaseModel):
    day: Day
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        parse_hm(value)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> "BlockedSlot":
        if parse_hm(self.end) <= parse_hm(self.start):
            raise ValueError("end debe ser posterior a start")
        return self

    def as_dict(self) -> dict:
        return {"day": self.day.value, "start": self.start, "end": self.end}


class ValidateRequest(BaseModel):
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    subject_ids: list[str] | None = Field(default=None, max_length=100)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)
    min_free_minutes: int = Field(default=30, ge=0, le=600)
    day_start: str | None = None
    day_end: str | None = None
    # ISO week (``2025-W43``) whose blocks, free slots and stats are returned.
    week: str | None = Field(default=None, pattern=r"^\d{4}-W\d{2}$")


class GenerateRequest(BaseModel):
    year_id: str | None = None
    subject_ids: list[str] | None = Field(default=None, max_length=100)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)
    pinned_group_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_partial: bool = True
    allow_conflicts: bool = True
    max_solutions: int = Field(default=60, ge=1, le=500)
    node_limit: int = Field(default=200_000, ge=1_000, le=2_000_000)

    @model_validator(mode="after")
    def _plan_or_year(self) -> "GenerateRequest":
        if self.year_id is None and not self.subject_ids:
            raise ValueError("Indica year_id o una lista no vacía de subject_ids")
        return self


class OptimizeRequest(BaseModel):
    subject_ids: list[str] = Field(min_length=1, max_length=100)
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)
    pinned_group_ids: list[str] = Field(default_factory=list, max_length=100)
    max_solutions: int = Field(default=60, ge=1, le=500)
    node_limit: int = Field(default=200_000, ge=1_000, le=2_000_000)


class FreeSlotsRequest(BaseModel):
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    ignore_session_ids: list[str] = Field(default_factory=list, max_length=100)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)
    duration_minutes: int = Field(default=60, ge=5, le=600)
    day_start: str | None = None
    day_end: str | None = None


class ShareRequest(BaseModel):
    year_id: str | None = None
    subject_ids: list[str] | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=120)
    group_ids: list[str] = Field(min_length=1, max_length=100)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)
    pinned_group_ids: list[str] = Field(default_factory=list, max_length=100)


class ICSRequest(BaseModel):
    group_ids: list[str] = Field(min_length=1, max_length=100)
    name: str = Field(default="Horario", max_length=80)
    custom_events: list[CustomEvent] = Field(default_factory=list, max_length=80)


def _blocked(payload: list[BlockedSlot]) -> list[dict]:
    return [slot.as_dict() for slot in payload]


def _events(payload: list[CustomEvent]) -> list[dict]:
    return [event.model_dump(mode="json") for event in payload]


def _window(day_start: str | None, day_end: str | None) -> tuple[str, str]:
    start = day_start or store.catalog.term.day_start
    end = day_end or store.catalog.term.day_end
    if parse_hm(end) <= parse_hm(start):
        raise HTTPException(status_code=422, detail="day_end debe ser posterior a day_start")
    return start, end


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@app.get("/api/health", tags=["meta"])
def health() -> dict:
    catalog = store.catalog
    return {
        "status": "ok",
        "version": __version__,
        "schema_version": catalog.schema_version,
        "generated_at": catalog.generated_at.isoformat(),
        "counts": {
            "degrees": len(catalog.degrees),
            "years": len(catalog.years),
            "subjects": len(catalog.subjects),
            "groups": len(catalog.groups),
            "class_sessions": len(catalog.class_sessions),
            "rooms": len(catalog.rooms),
        },
    }


@app.get("/health", include_in_schema=False)
def health_alias() -> dict:
    """Conventional alias for platforms that probe /health."""

    return health()


@app.get("/api/catalog", tags=["catalogue"])
def catalog() -> dict:
    return store.full_export()


@app.get("/api/catalog/export", tags=["catalogue"])
def export_catalog() -> FileResponse:
    return FileResponse(
        store.path,
        media_type="application/json",
        filename="horario-catalog.json",
    )


@app.get("/api/schema/catalog", tags=["catalogue"])
def catalog_schema() -> dict:
    return Catalog.model_json_schema()


@app.get("/api/sync/status", tags=["meta"])
def sync_status() -> dict:
    """Last live/synthetic USC snapshot, if `scripts/sync_usc.py` has run."""

    from .usc_source import last_run

    run = last_run()
    return {"available": run is not None, "last_run": run}


@app.get("/api/degrees", tags=["catalogue"])
def degrees() -> dict:
    return {
        "term": store.catalog.term.model_dump(mode="json"),
        "degrees": store.degree_summaries(),
    }


@app.get("/api/years/{year_id}", tags=["catalogue"])
def year_detail(year_id: str) -> dict:
    try:
        return store.year_view(year_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Curso no encontrado: {year_id}")


@app.get("/api/plan", tags=["catalogue"])
def plan(
    year_id: str | None = Query(default=None, description="Atajo para un curso completo"),
    subject_ids: str = Query(default="", description="ids de asignatura separados por comas"),
) -> dict:
    """Return an enriched plan that may mix academic years and degrees."""

    ids = [item.strip() for item in subject_ids.split(",") if item.strip()]
    if year_id:
        year = store.year_by_id.get(year_id)
        if year is None:
            raise HTTPException(status_code=404, detail=f"Curso no encontrado: {year_id}")
        # A year shortcut is a full-year plan; explicit subjects win if both are sent.
        if not ids:
            ids = list(year.subject_ids)
        elif not set(ids) <= set(year.subject_ids):
            raise HTTPException(
                status_code=422,
                detail="subject_ids contiene asignaturas de otro curso",
            )
    if not ids:
        raise HTTPException(status_code=422, detail="Indica year_id o subject_ids")
    try:
        return store.plan_view(ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


@app.post("/api/schedules/validate", tags=["scheduling"])
def validate(payload: ValidateRequest) -> dict:
    day_start, day_end = _window(payload.day_start, payload.day_end)
    try:
        return validate_selection(
            store,
            payload.group_ids,
            blocked=_blocked(payload.blocked),
            min_free_minutes=payload.min_free_minutes,
            day_start=day_start,
            day_end=day_end,
            subject_ids=payload.subject_ids,
            custom_events=_events(payload.custom_events),
            week=payload.week,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/schedules/generate", tags=["scheduling"])
def generate(payload: GenerateRequest) -> dict:
    try:
        return generate_schedule(
            store,
            payload.year_id,
            blocked=_blocked(payload.blocked),
            subject_ids=payload.subject_ids,
            allow_partial=payload.allow_partial,
            allow_conflicts=payload.allow_conflicts,
            max_solutions=payload.max_solutions,
            node_limit=payload.node_limit,
            custom_events=_events(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Curso no encontrado: {payload.year_id}")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/schedules/optimize", tags=["scheduling"])
def optimize(payload: OptimizeRequest) -> dict:
    try:
        return optimize_schedule(
            store,
            payload.group_ids,
            payload.subject_ids,
            blocked=_blocked(payload.blocked),
            custom_events=_events(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
            max_solutions=payload.max_solutions,
            node_limit=payload.node_limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/free-slots", tags=["scheduling"])
def free_slots(payload: FreeSlotsRequest) -> dict:
    day_start, day_end = _window(payload.day_start, payload.day_end)
    try:
        return free_slots_for_groups(
            store,
            payload.group_ids,
            ignore_session_ids=payload.ignore_session_ids,
            blocked=_blocked(payload.blocked),
            day_start=day_start,
            day_end=day_end,
            min_duration=payload.duration_minutes,
            custom_events=_events(payload.custom_events),
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# ---------------------------------------------------------------------------
# Sharing and export
# ---------------------------------------------------------------------------


@app.post("/api/schedules/share", tags=["sharing"], status_code=201)
def share(payload: ShareRequest) -> dict:
    try:
        record = store.save_shared(
            payload.group_ids,
            subject_ids=payload.subject_ids,
            title=payload.title,
            year_id=payload.year_id,
            custom_events=_events(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Curso no encontrado: {payload.year_id}")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    validation = validate_selection(
        store,
        record.group_ids,
        subject_ids=record.subject_ids or None,
        custom_events=[event.model_dump(mode="json") for event in record.custom_events],
    )
    return {
        "share": record.model_dump(mode="json"),
        "validation": validation,
        "url_path": f"/?s={record.id}",
    }


@app.get("/api/shared/{share_id}", tags=["sharing"])
def shared(share_id: str) -> dict:
    try:
        record = store.load_shared(share_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Horario compartido no encontrado")
    return {
        "share": record.model_dump(mode="json"),
        "validation": validate_selection(
            store,
            record.group_ids,
            subject_ids=record.subject_ids or None,
            custom_events=[
                event.model_dump(mode="json") for event in record.custom_events
            ],
        ),
    }


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _build_ics(ids: list[str], name: str, custom_events: list[dict] | None = None) -> Response:
    try:
        resolve_groups(store, ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    validation = validate_selection(
        store,
        ids,
        custom_events=custom_events or [],
    )
    term = store.catalog.term
    today = date.today()
    next_monday = today + timedelta(days=((7 - today.weekday()) % 7) or 7)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Horario USC//Constructor de horarios//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(name)}",
        f"X-WR-TIMEZONE:{term.timezone}",
    ]

    # One VEVENT per merged visual block, not one per hourly fragment.  A
    # block taught only in some weeks gets one dated VEVENT per week, moved to
    # the current academic year; a block without weeks repeats weekly.
    first_week = term.calendar[0].id if term.calendar else None
    occurrences: list[tuple[dict, date | None]] = []
    for block in validation["blocks"]:
        if block.get("weeks") and first_week:
            occurrences.extend(
                (block, week_in_current_year(week, first_week, today) + timedelta(days=WEEKDAY_INDEX[block["day"]]))
                for week in block["weeks"]
            )
        else:
            occurrences.append((block, None))

    for block, dated in occurrences:
        event_day = dated or next_monday + timedelta(days=WEEKDAY_INDEX[block["day"]])
        start = block["start"].replace(":", "")
        end = block["end"].replace(":", "")
        location = f"{block['building_name']} · {block['room_name']}"
        description = (
            f"{block['group_type_label']} · grupo {block['group_code']} · "
            f"{block['subject_name']}. "
        )
        if len(block.get("session_ids", [])) > 1:
            description += f"Bloque unido de {len(block['session_ids'])} sesiones. "
        description += "Generado con el constructor de horarios."
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{block['id']}{f'-{event_day:%Y%m%d}' if dated else ''}@horario.usc",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={term.timezone}:{event_day:%Y%m%d}T{start}00",
                f"DTEND;TZID={term.timezone}:{event_day:%Y%m%d}T{end}00",
                *(
                    []
                    if dated
                    else [f"RRULE:FREQ=WEEKLY;COUNT={term.weeks};BYDAY={ICS_DAY[block['day']]}"]
                ),
                f"SUMMARY:{_ics_escape(block['subject_name'])} ({block['group_code']})",
                f"LOCATION:{_ics_escape(location)}",
                f"DESCRIPTION:{_ics_escape(description)}",
                "END:VEVENT",
            ]
        )

    for event in validation.get("custom_events", []):
        event_day = next_monday + timedelta(days=WEEKDAY_INDEX[event["day"]])
        start = event["start"].replace(":", "")
        end = event["end"].replace(":", "")
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{_ics_escape(event['id'] or 'extra')}@horario.usc",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={term.timezone}:{event_day:%Y%m%d}T{start}00",
                f"DTEND;TZID={term.timezone}:{event_day:%Y%m%d}T{end}00",
                f"RRULE:FREQ=WEEKLY;COUNT={term.weeks};BYDAY={ICS_DAY[event['day']]}",
                f"SUMMARY:{_ics_escape(event['title'])}",
                f"LOCATION:{_ics_escape(event.get('location') or 'Extraescolar')}",
                "DESCRIPTION:Actividad extraescolar semanal.",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    filename = f"{slugify(name)}.ics"
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/schedules/export.ics", tags=["export"])
def export_ics(
    group_ids: str = Query(default="", description="ids de grupo separados por comas"),
    name: str = Query(default="Horario", max_length=80),
) -> Response:
    ids = [item.strip() for item in group_ids.split(",") if item.strip()]
    return _build_ics(ids, name)


@app.post("/api/schedules/export.ics", tags=["export"])
def export_ics_with_events(payload: ICSRequest) -> Response:
    return _build_ics(
        payload.group_ids,
        payload.name,
        custom_events=_events(payload.custom_events),
    )


# ---------------------------------------------------------------------------
# Static SPA (mounted last so /api keeps priority)
# ---------------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": f"No encontrado: {exc}"})
