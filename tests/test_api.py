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

    year = client.get("/api/years/year-matematicas-1")
    assert year.status_code == 200
    assert len(year.json()["subjects"]) == 4
    assert year.json()["subjects"][0]["groups"][0]["sessions"]


def test_unknown_year_returns_404() -> None:
    response = client.get("/api/years/year-nope")
    assert response.status_code == 404


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
    response = client.post(
        "/api/schedules/share",
        json={"year_id": "year-matematicas-1", "group_ids": group_ids},
    )
    assert response.status_code == 201
    payload = response.json()
    share_id = payload["share"]["id"]
    assert payload["url_path"] == f"/?s={share_id}"
    try:
        loaded = client.get(f"/api/shared/{share_id}")
        assert loaded.status_code == 200
        assert loaded.json()["share"]["group_ids"] == group_ids
        assert loaded.json()["validation"]["group_ids"] == group_ids
    finally:
        (SHARED_DIR / f"{share_id}.json").unlink(missing_ok=True)


def test_share_rejects_groups_from_another_year() -> None:
    other = store.subject_by_id[store.year_by_id["year-matematicas-2"].subject_ids[0]]
    response = client.post(
        "/api/schedules/share",
        json={"year_id": "year-matematicas-1", "group_ids": [other.group_ids[0]]},
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


def test_frontend_is_served() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Constructor de horarios" in response.text
