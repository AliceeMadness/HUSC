"""HTTP contract tests using FastAPI's TestClient (no running server needed)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.main import app
from backend.store import SHARED_DIR, store

client = TestClient(app)


def _first_year_groups() -> list[str]:
    year = store.year_by_id["year-matematicas-1"]
    result = []
    for subject_id in year.subject_ids:
        subject = store.subject_by_id[subject_id]
        seen_types = set()
        for group_id in subject.group_ids:
            group = store.group_by_id[group_id]
            if group.type not in seen_types:
                result.append(group_id)
                seen_types.add(group.type)
    return result


def test_health_and_counts() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["counts"]["class_sessions"] == 386
    assert payload["schema_version"] == "2.0"
    assert client.get("/health").json()["status"] == "ok"


def test_sync_status_endpoint() -> None:
    response = client.get("/api/sync/status")
    assert response.status_code == 200
    assert "available" in response.json()
    assert "last_run" in response.json()


def test_catalog_and_schema() -> None:
    response = client.get("/api/catalog")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "2.0"

    schema = client.get("/api/schema/catalog")
    assert schema.status_code == 200
    assert schema.json()["title"] == "Catalog"
    assert "class_sessions" in schema.json()["properties"]


def test_catalog_export_is_an_attachment() -> None:
    response = client.get("/api/catalog/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("application/json")


def test_degrees_and_year_view() -> None:
    degrees = client.get("/api/degrees")
    assert degrees.status_code == 200
    assert len(degrees.json()["degrees"]) == 2
    assert degrees.json()["term"]["timezone"] == "Europe/Madrid"

    assert degrees.json()["degrees"][0]["years"][0]["subjects"]

    year = client.get("/api/years/year-matematicas-1")
    assert year.status_code == 200
    assert len(year.json()["subjects"]) == 4
    assert year.json()["subjects"][0]["groups"][0]["sessions"]


def test_plan_can_mix_years_and_degrees() -> None:
    math = store.year_by_id["year-matematicas-1"].subject_ids[0]
    physics = store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]

    by_year = client.get("/api/plan", params={"year_id": "year-matematicas-1"})
    assert by_year.status_code == 200
    assert len(by_year.json()["subjects"]) == 4

    mixed = client.get("/api/plan", params={"subject_ids": f"{math},{physics}"})
    assert mixed.status_code == 200
    payload = mixed.json()
    assert {subject["id"] for subject in payload["subjects"]} == {math, physics}
    assert len(payload["years"]) == 2
    assert len(payload["degrees"]) == 2

    empty = client.get("/api/plan")
    assert empty.status_code == 422
    assert (
        client.get("/api/plan", params={"subject_ids": "sub-nope"}).status_code == 422
    )


def test_unknown_year_returns_404() -> None:
    response = client.get("/api/years/year-nope")
    assert response.status_code == 404
    assert client.get("/api/plan", params={"year_id": "year-nope"}).status_code == 404


def test_generate_endpoint() -> None:
    response = client.post("/api/schedules/generate", json={"year_id": "year-matematicas-1"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "optimal"
    assert payload["stats"]["conflict_count"] == 0
    assert len(payload["group_ids"]) == 8
    assert payload["assignments"]


def test_generate_partial_endpoint() -> None:
    response = client.post(
        "/api/schedules/generate", json={"year_id": "year-matematicas-fisica-4"}
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["excluded_subjects"]
    assert payload["stats"]["conflict_count"] == 0


def test_generate_cross_year_plan_endpoint() -> None:
    math = store.year_by_id["year-matematicas-1"].subject_ids[0]
    physics = store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]
    response = client.post(
        "/api/schedules/generate",
        json={"subject_ids": [math, physics]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "optimal"
    assert {assignment["subject_id"] for assignment in payload["assignments"]} == {
        math,
        physics,
    }
    assert len(payload["years"]) == 2


def test_generate_requires_plan_or_year() -> None:
    response = client.post("/api/schedules/generate", json={})
    assert response.status_code == 422


def test_generate_invalid_year() -> None:
    response = client.post("/api/schedules/generate", json={"year_id": "year-nope"})
    assert response.status_code == 404


def test_validate_endpoint_and_blocked_conflicts() -> None:
    group_ids = _first_year_groups()
    response = client.post("/api/schedules/validate", json={"group_ids": group_ids})
    assert response.status_code == 200
    payload = response.json()
    assert payload["stats"]["total_sessions"] > 0
    assert "free_slots" in payload

    first_group = store.group_by_id[group_ids[0]]
    session = store.sessions_by_group[first_group.id][0]
    blocked = [{"day": session.day.value, "start": session.start, "end": session.end}]
    blocked_response = client.post(
        "/api/schedules/validate", json={"group_ids": [first_group.id], "blocked": blocked}
    )
    assert blocked_response.status_code == 200
    assert blocked_response.json()["blocked_conflicts"]


def test_validate_unknown_group_returns_422() -> None:
    response = client.post("/api/schedules/validate", json={"group_ids": ["grp-nope"]})
    assert response.status_code == 422
    assert "desconocidos" in response.json()["detail"]


def test_validate_uses_the_explicit_plan_for_missing_groups() -> None:
    first = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    second = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    response = client.post(
        "/api/schedules/validate",
        json={"group_ids": [first.group_ids[0]], "subject_ids": [first.id, second.id]},
    )
    assert response.status_code == 200
    missing = response.json()["missing"]
    assert {item["subject_id"] for item in missing} == {first.id, second.id}
    assert not any(
        item["subject_id"] == store.year_by_id["year-matematicas-1"].subject_ids[1]
        for item in missing
    )


def test_validate_and_generate_with_custom_events_and_pins() -> None:
    group_ids = _first_year_groups()
    first_group = store.group_by_id[group_ids[0]]
    session = store.sessions_by_group[first_group.id][0]
    event = {
        "id": "evt-api",
        "title": "Trabajo",
        "day": session.day.value,
        "start": session.start,
        "end": session.end,
        "color": "#7c5cbf",
    }

    validation = client.post(
        "/api/schedules/validate",
        json={"group_ids": group_ids, "custom_events": [event]},
    )
    assert validation.status_code == 200
    assert validation.json()["event_conflicts"]
    assert validation.json()["stats"]["event_conflict_count"] >= 1

    generated = client.post(
        "/api/schedules/generate",
        json={
            "year_id": "year-matematicas-1",
            "pinned_group_ids": [first_group.id],
            "custom_events": [
                {
                    "id": "evt-api-2",
                    "title": "Entrenamiento",
                    "day": "mon",
                    "start": "18:00",
                    "end": "20:00",
                }
            ],
        },
    )
    assert generated.status_code == 200
    payload = generated.json()
    assert first_group.id in payload["group_ids"]
    assert payload["stats"]["event_conflict_count"] == 0
    assert payload["blocks"]


def test_free_slots_endpoint_with_event() -> None:
    group_ids = _first_year_groups()[:2]
    event = {
        "id": "evt-free",
        "title": "Academia",
        "day": "fri",
        "start": "10:00",
        "end": "12:00",
    }
    response = client.post(
        "/api/free-slots",
        json={"group_ids": group_ids, "duration_minutes": 30, "custom_events": [event]},
    )
    assert response.status_code == 200
    for slot in response.json()["slots"]:
        assert not (
            slot["day"] == "fri"
            and slot["start"] <= "10:00"
            and slot["end"] >= "12:00"
        )


def test_free_slots_endpoint() -> None:
    group_ids = _first_year_groups()
    response = client.post(
        "/api/free-slots",
        json={"group_ids": group_ids, "duration_minutes": 60},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["slots"]
    assert all(slot["duration_minutes"] >= 60 for slot in payload["slots"])
    assert payload["occupied"]


def test_share_and_load() -> None:
    group_ids = _first_year_groups()
    subject_ids = list(store.year_by_id["year-matematicas-1"].subject_ids)
    response = client.post(
        "/api/schedules/share",
        json={
            "year_id": "year-matematicas-1",
            "subject_ids": subject_ids,
            "title": "Primero de Matemáticas",
            "group_ids": group_ids,
            "pinned_group_ids": [group_ids[0]],
            "custom_events": [
                {
                    "id": "evt-share",
                    "title": "Trabajo",
                    "day": "fri",
                    "start": "18:00",
                    "end": "20:00",
                    "color": "#7c5cbf",
                }
            ],
        },
    )
    assert response.status_code == 201
    payload = response.json()
    share_id = payload["share"]["id"]
    assert payload["url_path"] == f"/?s={share_id}"
    try:
        loaded = client.get(f"/api/shared/{share_id}")
        assert loaded.status_code == 200
        assert loaded.json()["share"]["group_ids"] == group_ids
        assert loaded.json()["share"]["subject_ids"] == subject_ids
        assert loaded.json()["share"]["title"] == "Primero de Matemáticas"
        assert loaded.json()["share"]["pinned_group_ids"] == [group_ids[0]]
        assert loaded.json()["share"]["custom_events"][0]["title"] == "Trabajo"
        assert loaded.json()["validation"]["group_ids"] == group_ids
    finally:
        (SHARED_DIR / f"{share_id}.json").unlink(missing_ok=True)


def test_share_supports_cross_year_plans() -> None:
    math = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    physics = store.subject_by_id[
        store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]
    ]
    group_ids = [math.group_ids[0], physics.group_ids[0]]
    response = client.post(
        "/api/schedules/share",
        json={
            "subject_ids": [math.id, physics.id],
            "title": "Matemáticas + Física",
            "group_ids": group_ids,
        },
    )
    assert response.status_code == 201
    payload = response.json()
    assert set(payload["share"]["subject_ids"]) == {math.id, physics.id}
    assert payload["share"]["year_id"] is None
    try:
        loaded = client.get(f"/api/shared/{payload['share']['id']}")
        assert loaded.status_code == 200
        assert set(loaded.json()["share"]["subject_ids"]) == {math.id, physics.id}
    finally:
        (SHARED_DIR / f"{payload['share']['id']}.json").unlink(missing_ok=True)


def test_share_rejects_groups_outside_the_plan() -> None:
    math = store.subject_by_id[store.year_by_id["year-matematicas-1"].subject_ids[0]]
    physics = store.subject_by_id[
        store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]
    ]
    response = client.post(
        "/api/schedules/share",
        json={
            "subject_ids": [math.id],
            "group_ids": [physics.group_ids[0]],
        },
    )
    assert response.status_code == 422


def test_missing_shared_schedule_returns_404() -> None:
    assert client.get("/api/shared/deadbeef").status_code == 404
    assert client.get("/api/shared/../../etc/passwd").status_code == 404


def test_ics_export() -> None:
    group_id = _first_year_groups()[0]
    response = client.get(
        "/api/schedules/export.ics",
        params={"group_ids": group_id, "name": "Horario USC"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"]
    assert "BEGIN:VCALENDAR" in response.text
    assert "RRULE:FREQ=WEEKLY" in response.text
    assert "END:VCALENDAR" in response.text


def test_ics_export_includes_extracurricular_events() -> None:
    group_ids = _first_year_groups()
    response = client.post(
        "/api/schedules/export.ics",
        json={
            "group_ids": group_ids,
            "name": "Horario con extraescolar",
            "custom_events": [
                {
                    "id": "evt-ics",
                    "title": "Trabajo",
                    "day": "fri",
                    "start": "18:00",
                    "end": "20:00",
                    "location": "Oficina",
                }
            ],
        },
    )
    assert response.status_code == 200
    assert "SUMMARY:Trabajo" in response.text
    assert "LOCATION:Oficina" in response.text


def test_frontend_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Constructor de horarios" in response.text
