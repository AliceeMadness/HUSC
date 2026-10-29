"""FastAPI application: JSON API + static single-page frontend.

Request bodies live in ``schemas.py``, the algorithms in ``scheduling/`` and
the calendar export in ``ics.py``; this module only maps HTTP to them.
``ValueError`` becomes 422 and ``KeyError`` 404, with a Spanish ``detail``
that the frontend shows to the student.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .ics import build_calendar
from .ids import slugify
from .models import Catalog
from .scheduling import (
    free_slots_for_groups,
    generate_schedule,
    optimize_schedule,
    resolve_groups,
    validate_selection,
)
from .schemas import (
    FreeSlotsRequest,
    GenerateRequest,
    ICSRequest,
    OptimizeRequest,
    ShareRequest,
    ValidateRequest,
    blocked_dicts,
    event_dicts,
)
from .store import ROOT, store
from .timeutil import parse_hm

FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(
    title="Horario USC API",
    version=__version__,
    description=(
        "Constructor de horarios para los grados de Matemáticas, Física, Ingeniería "
        "Informática, Inteligencia Artificial y sus dobles grados en la USC. Catálogo "
        "versionado, validación de solapes semana a semana, reparación de solapes y "
        "exportación a calendario."
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


def _window(day_start: str | None, day_end: str | None) -> tuple[str, str]:
    start = day_start or store.catalog.term.day_start
    end = day_end or store.catalog.term.day_end
    if parse_hm(end) <= parse_hm(start):
        raise HTTPException(status_code=422, detail="day_end debe ser posterior a day_start")
    return start, end


def _unprocessable(error: ValueError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(error))


def _year_not_found(year_id: str | None) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Curso no encontrado: {year_id}")


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
    return FileResponse(store.path, media_type="application/json", filename="horario-catalog.json")


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
        raise _year_not_found(year_id)


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
            raise _year_not_found(year_id)
        # A year shortcut is a full-year plan; explicit subjects win if both are sent.
        if not ids:
            ids = list(year.subject_ids)
        elif not set(ids) <= set(year.subject_ids):
            raise HTTPException(status_code=422, detail="subject_ids contiene asignaturas de otro curso")
    if not ids:
        raise HTTPException(status_code=422, detail="Indica year_id o subject_ids")
    try:
        return store.plan_view(ids)
    except ValueError as error:
        raise _unprocessable(error) from error


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
            blocked=blocked_dicts(payload.blocked),
            min_free_minutes=payload.min_free_minutes,
            day_start=day_start,
            day_end=day_end,
            subject_ids=payload.subject_ids,
            custom_events=event_dicts(payload.custom_events),
            week=payload.week,
        )
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/schedules/generate", tags=["scheduling"])
def generate(payload: GenerateRequest) -> dict:
    try:
        return generate_schedule(
            store,
            payload.year_id,
            blocked=blocked_dicts(payload.blocked),
            subject_ids=payload.subject_ids,
            allow_partial=payload.allow_partial,
            allow_conflicts=payload.allow_conflicts,
            max_solutions=payload.max_solutions,
            node_limit=payload.node_limit,
            custom_events=event_dicts(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
        )
    except KeyError:
        raise _year_not_found(payload.year_id)
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/schedules/optimize", tags=["scheduling"])
def optimize(payload: OptimizeRequest) -> dict:
    try:
        return optimize_schedule(
            store,
            payload.group_ids,
            payload.subject_ids,
            blocked=blocked_dicts(payload.blocked),
            custom_events=event_dicts(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
            max_solutions=payload.max_solutions,
            node_limit=payload.node_limit,
        )
    except ValueError as error:
        raise _unprocessable(error) from error


@app.post("/api/free-slots", tags=["scheduling"])
def free_slots(payload: FreeSlotsRequest) -> dict:
    day_start, day_end = _window(payload.day_start, payload.day_end)
    try:
        return free_slots_for_groups(
            store,
            payload.group_ids,
            ignore_session_ids=payload.ignore_session_ids,
            blocked=blocked_dicts(payload.blocked),
            day_start=day_start,
            day_end=day_end,
            min_duration=payload.duration_minutes,
            custom_events=event_dicts(payload.custom_events),
        )
    except ValueError as error:
        raise _unprocessable(error) from error


# ---------------------------------------------------------------------------
# Sharing and export
# ---------------------------------------------------------------------------


def _validate_shared(record) -> dict:
    return validate_selection(
        store,
        record.group_ids,
        subject_ids=record.subject_ids or None,
        custom_events=[event.model_dump(mode="json") for event in record.custom_events],
    )


@app.post("/api/schedules/share", tags=["sharing"], status_code=201)
def share(payload: ShareRequest) -> dict:
    try:
        record = store.save_shared(
            payload.group_ids,
            subject_ids=payload.subject_ids,
            title=payload.title,
            year_id=payload.year_id,
            custom_events=event_dicts(payload.custom_events),
            pinned_group_ids=payload.pinned_group_ids,
        )
    except KeyError:
        raise _year_not_found(payload.year_id)
    except ValueError as error:
        raise _unprocessable(error) from error
    return {
        "share": record.model_dump(mode="json"),
        "validation": _validate_shared(record),
        "url_path": f"/?s={record.id}",
    }


@app.get("/api/shared/{share_id}", tags=["sharing"])
def shared(share_id: str) -> dict:
    try:
        record = store.load_shared(share_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Horario compartido no encontrado")
    return {"share": record.model_dump(mode="json"), "validation": _validate_shared(record)}


def _ics_response(ids: list[str], name: str, custom_events: list[dict] | None = None) -> Response:
    try:
        resolve_groups(store, ids)
    except ValueError as error:
        raise _unprocessable(error) from error
    validation = validate_selection(store, ids, custom_events=custom_events or [])
    return Response(
        content=build_calendar(validation, store.catalog.term, name),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{slugify(name)}.ics"'},
    )


@app.get("/api/schedules/export.ics", tags=["export"])
def export_ics(
    group_ids: str = Query(default="", description="ids de grupo separados por comas"),
    name: str = Query(default="Horario", max_length=80),
) -> Response:
    ids = [item.strip() for item in group_ids.split(",") if item.strip()]
    return _ics_response(ids, name)


@app.post("/api/schedules/export.ics", tags=["export"])
def export_ics_with_events(payload: ICSRequest) -> Response:
    return _ics_response(payload.group_ids, payload.name, custom_events=event_dicts(payload.custom_events))


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
