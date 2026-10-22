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
from .models import Catalog, Day
from .scheduling import (
    free_slots_for_groups,
    generate_schedule,
    resolve_groups,
    sessions_for_groups,
    validate_selection,
)
from .store import ROOT, store
from .timeutil import DAY_LABELS, parse_hm

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
    blocked: list[BlockedSlot] = Field(default_factory=list)
    min_free_minutes: int = Field(default=30, ge=0, le=600)
    day_start: str | None = None
    day_end: str | None = None


class GenerateRequest(BaseModel):
    year_id: str
    subject_ids: list[str] | None = Field(default=None, max_length=30)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    allow_partial: bool = True
    allow_conflicts: bool = True
    max_solutions: int = Field(default=60, ge=1, le=500)
    node_limit: int = Field(default=200_000, ge=1_000, le=2_000_000)


class FreeSlotsRequest(BaseModel):
    group_ids: list[str] = Field(default_factory=list, max_length=100)
    ignore_session_ids: list[str] = Field(default_factory=list, max_length=100)
    blocked: list[BlockedSlot] = Field(default_factory=list)
    duration_minutes: int = Field(default=60, ge=5, le=600)
    day_start: str | None = None
    day_end: str | None = None


class ShareRequest(BaseModel):
    year_id: str
    group_ids: list[str] = Field(min_length=1, max_length=100)


def _blocked(payload: list[BlockedSlot]) -> list[dict]:
    return [slot.as_dict() for slot in payload]


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
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Curso no encontrado: {payload.year_id}")
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
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


# ---------------------------------------------------------------------------
# Sharing and export
# ---------------------------------------------------------------------------


@app.post("/api/schedules/share", tags=["sharing"], status_code=201)
def share(payload: ShareRequest) -> dict:
    try:
        record = store.save_shared(payload.year_id, payload.group_ids)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Curso no encontrado: {payload.year_id}")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    validation = validate_selection(store, record.group_ids)
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
        "validation": validate_selection(store, record.group_ids),
    }


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


@app.get("/api/schedules/export.ics", tags=["export"])
def export_ics(
    group_ids: str = Query(default="", description="ids de grupo separados por comas"),
    name: str = Query(default="Horario", max_length=80),
) -> Response:
    ids = [item.strip() for item in group_ids.split(",") if item.strip()]
    try:
        groups = resolve_groups(store, ids)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    sessions = sessions_for_groups(store, groups)
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
    for session in sessions:
        subject = store.subject_by_id[session.subject_id]
        group = store.group_by_id[session.group_id]
        room = store.room_by_id[session.room_id]
        building = store.building_by_id[session.building_id]
        event_day = next_monday + timedelta(days=WEEKDAY_INDEX[session.day.value])
        start = session.start.replace(":", "")
        end = session.end.replace(":", "")
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{session.id}@horario.usc",
                f"DTSTAMP:{stamp}",
                f"DTSTART;TZID={term.timezone}:{event_day:%Y%m%d}T{start}00",
                f"DTEND;TZID={term.timezone}:{event_day:%Y%m%d}T{end}00",
                f"RRULE:FREQ=WEEKLY;COUNT={term.weeks};BYDAY={ICS_DAY[session.day.value]}",
                f"SUMMARY:{_ics_escape(subject.name)} ({group.code})",
                f"LOCATION:{_ics_escape(f'{building.name} · {room.name}')}",
                (
                    "DESCRIPTION:"
                    + _ics_escape(
                        f"{group.type_label} · grupo {group.code} · {subject.name}. "
                        "Generado con el constructor de horarios."
                    )
                ),
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
