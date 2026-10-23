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
    assert payload["counts"]["class_sessions"] >= 1400
    assert payload["counts"]["degrees"] == 6
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
    assert len(degrees.json()["degrees"]) == 6
    assert degrees.json()["term"]["timezone"] == "Europe/Madrid"

    assert degrees.json()["degrees"][0]["years"][0]["subjects"]

    year = client.get("/api/years/year-matematicas-1")
    assert year.status_code == 200
    subjects = year.json()["subjects"]
    assert [s["semester"] for s in subjects].count(1) == 4
    assert [s["semester"] for s in subjects].count(2) == 4
    assert year.json()["subjects"][0]["groups"][0]["sessions"]


def test_plan_can_mix_years_and_degrees() -> None:
    math = store.year_by_id["year-matematicas-1"].subject_ids[0]
    physics = store.year_by_id["year-matematicas-fisica-1"].subject_ids[0]

    by_year = client.get("/api/plan", params={"year_id": "year-matematicas-1"})
    assert by_year.status_code == 200
    assert [s["semester"] for s in by_year.json()["subjects"]].count(1) == 4

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
    expected_groups = sum(
        len({store.group_by_id[group_id].type for group_id in store.subject_by_id[subject_id].group_ids})
        for subject_id in store.year_by_id["year-matematicas-1"].subject_ids
    )
    assert len(payload["group_ids"]) == expected_groups
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
    assert "END:VCALENDAR" in response.text
    # Classes with known weeks get one dated event per week instead of a
    # weekly rule, so special weeks are exported as they are taught.
    blocks = client.post("/api/schedules/validate", json={"group_ids": [group_id]}).json()["blocks"]
    expected = sum(len(block["weeks"]) if block["weeks"] else 1 for block in blocks)
    assert response.text.count("BEGIN:VEVENT") == expected
    assert ("RRULE:FREQ=WEEKLY" in response.text) == any(not block["weeks"] for block in blocks)


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


def _first_groups(year_id: str) -> list[str]:
    result = []
    for subject_id in store.year_by_id[year_id].subject_ids:
        seen_types = set()
        for group_id in store.subject_by_id[subject_id].group_ids:
            group = store.group_by_id[group_id]
            if group.type not in seen_types:
                result.append(group_id)
                seen_types.add(group.type)
    return result


def test_optimize_keeps_conflict_free_schedule() -> None:
    group_ids = _first_year_groups()
    response = client.post(
        "/api/schedules/optimize",
        json={
            "subject_ids": store.year_by_id["year-matematicas-1"].subject_ids,
            "group_ids": group_ids,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unchanged"
    assert set(payload["group_ids"]) == set(group_ids)
    assert payload["changes"] == []


def test_optimize_removes_overlaps_with_few_changes() -> None:
    year_id = "year-matematicas-fisica-3"
    group_ids = _first_groups(year_id)
    response = client.post(
        "/api/schedules/optimize",
        json={"subject_ids": store.year_by_id[year_id].subject_ids, "group_ids": group_ids},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "optimized"
    assert payload["conflicts"] == []
    kept = set(group_ids) & set(payload["group_ids"])
    assert len(kept) == len(group_ids) - payload["solver"]["changed"]
    assert len(payload["changes"]) == payload["solver"]["changed"]


def test_optimize_never_moves_pinned_groups() -> None:
    year_id = "year-matematicas-fisica-3"
    group_ids = _first_groups(year_id)
    baseline = client.post(
        "/api/schedules/optimize",
        json={"subject_ids": store.year_by_id[year_id].subject_ids, "group_ids": group_ids},
    ).json()
    moved = baseline["changes"][0]["from_group_id"]
    response = client.post(
        "/api/schedules/optimize",
        json={
            "subject_ids": store.year_by_id[year_id].subject_ids,
            "group_ids": group_ids,
            "pinned_group_ids": [moved],
        },
    )
    payload = response.json()
    assert moved in payload["group_ids"]
    assert payload["conflicts"] == []


def test_optimize_rejects_groups_outside_plan() -> None:
    response = client.post(
        "/api/schedules/optimize",
        json={
            "subject_ids": store.year_by_id["year-matematicas-1"].subject_ids,
            "group_ids": _first_groups("year-fisica-1")[:1],
        },
    )
    assert response.status_code == 422


def _second_group_of_same_type(group_ids: list[str]) -> str | None:
    """A sibling (same subject and type) of a chosen group that clashes with nothing."""

    for group_id in group_ids:
        group = store.group_by_id[group_id]
        for sibling_id in store.subject_by_id[group.subject_id].group_ids:
            sibling = store.group_by_id[sibling_id]
            if sibling_id in group_ids or sibling.type != group.type:
                continue
            check = client.post(
                "/api/schedules/validate",
                json={"group_ids": [*group_ids, sibling_id]},
            ).json()
            if check["ok"]:
                return sibling_id
    return None


def test_several_groups_of_same_type_can_be_selected() -> None:
    # A lecturer may teach CLE01 and CLE02 of the same subject.
    year_id = "year-matematicas-1"
    group_ids = _first_year_groups()
    extra = _second_group_of_same_type(group_ids)
    assert extra is not None
    payload = client.post(
        "/api/schedules/validate",
        json={
            "group_ids": [*group_ids, extra],
            "subject_ids": store.year_by_id[year_id].subject_ids,
        },
    ).json()
    assert extra in payload["group_ids"]
    assert payload["missing"] == []
    subject_id = store.group_by_id[extra].subject_id
    selection = next(item for item in payload["selections"] if item["subject_id"] == subject_id)
    same_type = [item for item in selection["groups"] if item["type"] == store.group_by_id[extra].type.value]
    assert len(same_type) == 2


def test_optimize_keeps_every_group_of_the_same_type() -> None:
    year_id = "year-matematicas-1"
    group_ids = _first_year_groups()
    extra = _second_group_of_same_type(group_ids)
    assert extra is not None
    response = client.post(
        "/api/schedules/optimize",
        json={
            "subject_ids": store.year_by_id[year_id].subject_ids,
            "group_ids": [*group_ids, extra],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unchanged"
    assert set(payload["group_ids"]) == {*group_ids, extra}


def test_optimize_resolves_clash_between_groups_of_same_type() -> None:
    # Two selected groups of one type that overlap are two classes: the
    # repair moves one of them instead of collapsing them into one.
    year_id = "year-matematicas-fisica-3"
    group_ids = _first_groups(year_id)
    baseline = client.post(
        "/api/schedules/optimize",
        json={"subject_ids": store.year_by_id[year_id].subject_ids, "group_ids": group_ids},
    ).json()
    pinned = baseline["changes"][0]["from_group_id"]
    response = client.post(
        "/api/schedules/optimize",
        json={
            "subject_ids": store.year_by_id[year_id].subject_ids,
            "group_ids": [*group_ids, baseline["changes"][0]["to_group_id"]],
            "pinned_group_ids": [pinned],
        },
    )
    payload = response.json()
    assert payload["conflicts"] == []
    assert pinned in payload["group_ids"]
    final = [store.group_by_id[group_id] for group_id in payload["group_ids"]]
    assert len(final) == len(set(payload["group_ids"]))


def test_optimize_only_touches_chosen_subjects_and_types() -> None:
    # Picking only the CLE of each subject must not bring in its CLIL/CLIS,
    # and subjects without any group stay empty.
    year_id = "year-matematicas-fisica-3"
    subject_ids = store.year_by_id[year_id].subject_ids
    lectures = [
        group_id
        for group_id in _first_groups(year_id)
        if store.group_by_id[group_id].type.value == "expositive"
    ]
    chosen = lectures[:-1]
    response = client.post(
        "/api/schedules/optimize",
        json={"subject_ids": subject_ids, "group_ids": chosen},
    )
    assert response.status_code == 200
    payload = response.json()
    allowed = {
        (store.group_by_id[group_id].subject_id, store.group_by_id[group_id].type)
        for group_id in chosen
    }
    result = {
        (store.group_by_id[group_id].subject_id, store.group_by_id[group_id].type)
        for group_id in payload["group_ids"]
    }
    assert result <= allowed
    assert all(change["from_group_id"] for change in payload["changes"])


def test_optimize_without_groups_changes_nothing() -> None:
    year_id = "year-matematicas-1"
    payload = client.post(
        "/api/schedules/optimize",
        json={"subject_ids": store.year_by_id[year_id].subject_ids, "group_ids": []},
    ).json()
    assert payload["status"] == "unchanged"
    assert payload["group_ids"] == []
