"""Tests for the USC AJAX parser and the synthetic SQLite fallback."""

from __future__ import annotations

import json

from backend.usc_source import (
    FORMS_PATH,
    last_run,
    legacy_slot,
    parse_calendar_html,
    sync,
    synthetic_rows,
)


SAMPLE_HTML = """
<div id="course-detail-controller">
  <article class="calendar-day">
    <header class="calendar-day-header"><h3>Lunes</h3></header>
    <ul class="calendar-day-time-group day-time-group-10">
      <li class="expository-group">
        <article class="ml-academic-subject">
          <h3 class="at-title"><a href="#">Topología   de los espacios</a></h3>
          <ul class="academic-subject-specs-list">
            <li>Grupo/CLE_02<br>Aula 06</li>
            <li>10:00-11:00</li>
          </ul>
        </article>
      </li>
      <li class="laboratory-group">
        <article class="ml-academic-subject">
          <h3 class="at-title"><a href="#">Topología de los espacios</a></h3>
          <ul class="academic-subject-specs-list">
            <li>Grupo/CLIL_02<br>Aula 09</li>
            <li>10:00-11:00</li>
          </ul>
        </article>
      </li>
    </ul>
  </article>
</div>
"""


def test_parse_calendar_fragment_normalises_rows() -> None:
    rows = parse_calendar_html(SAMPLE_HTML, "Primero")
    assert len(rows) == 2
    assert rows[0] == {
        "course": "Primero",
        "day": "mon",
        "start": "10:00",
        "end": "11:00",
        "subject": "Topología de los espacios",
        "group": "CLE02",
        "room": "Aula 06",
    }
    assert rows[1]["group"] == "CLIL02"
    assert rows[1]["room"] == "Aula 09"
    assert legacy_slot(rows[0]) == "L10"


def test_parse_calendar_ignores_subjects_without_group() -> None:
    html = SAMPLE_HTML.replace("Grupo/CLE_02<br>Aula 06", "").replace(
        "Grupo/CLIL_02<br>Aula 09", ""
    )
    assert parse_calendar_html(html, "Primero") == []


def test_synthetic_rows_are_valid_and_deterministic() -> None:
    forms = json.loads(FORMS_PATH.read_text(encoding="utf-8"))
    first = synthetic_rows("Primero", forms)
    second = synthetic_rows("Primero", forms)
    assert first == second
    assert first
    assert all(row["course"] == "Primero" for row in first)
    assert all(row["start"] < row["end"] for row in first)
    # Every subject gets exactly one group slot, and different subjects do not
    # share a slot in the synthetic fixture.
    slots_by_subject: dict[str, set[tuple[str, str]]] = {}
    for row in first:
        if row["group"].startswith("CLE"):
            slots_by_subject.setdefault(row["subject"], set()).add(
                (row["day"], row["start"])
            )
    assert all(len(slots) == 1 for slots in slots_by_subject.values())
    assert len({next(iter(slots)) for slots in slots_by_subject.values()}) == len(
        slots_by_subject
    )


def test_sync_fallback_writes_sqlite_and_catalogue(tmp_path) -> None:
    db_path = tmp_path / "sync.db"
    csv_path = tmp_path / "sync.csv"
    catalog_path = tmp_path / "catalog.json"
    result = sync(
        force_synthetic=True,
        db_path=db_path,
        csv_path=csv_path,
        catalog_path=catalog_path,
    )
    assert result["mode"] == "synthetic"
    assert result["sessions"] > 0
    assert db_path.exists()
    assert csv_path.exists()
    assert catalog_path.exists()
    run = last_run(db_path)
    assert run is not None
    assert run["mode"] == "synthetic"
    assert run["session_count"] == result["sessions"]
    assert run["by_source"] == {"synthetic": result["sessions"]}
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert catalog["source"]["notes"]


def test_week_paths_cross_new_year() -> None:
    from backend.usc_source import week_id, week_range

    assert week_range("25/51", "26/2") == ["25/51", "25/52", "26/1", "26/2"]
    assert week_id("25/43") == "2025-W43"
    assert week_id("26/5") == "2026-W05"


def test_weekly_sync_keeps_the_weeks_of_each_class(tmp_path, monkeypatch) -> None:
    import yaml

    from backend import usc_source

    config = {
        "term": {
            "id": "test",
            "label": "Test",
            "academic_year": "2025/26",
            "semesters": [
                {"number": 1, "week": "25/40", "first_week": "25/40", "last_week": "25/42"},
            ],
        },
        "degrees": [
            {
                "id": "matematicas",
                "name": "Grado en Matemáticas",
                "short_name": "Matemáticas",
                "building": "Facultad de Matemáticas",
                "years": [{"number": 1, "name": "Primero", "sources": [{"url": "https://x/25/43?a"}]}],
            }
        ],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")

    def row(start: str, end: str, group: str = "CLE01") -> dict:
        return {"course": "", "day": "mon", "start": start, "end": end,
                "subject": "Álgebra", "group": group, "room": "Aula 1"}

    # 25/40 and 25/42 are regular; 25/41 is a holiday week without classes,
    # and an extra lab only happens in 25/42.
    weeks = {
        "/25/40?": [row("09:00", "10:00")],
        "/25/41?": [],
        "/25/42?": [row("09:00", "10:00"), row("12:00", "14:00", "CLIL01")],
    }

    def fake_fetch(url, course_label, timeout=30.0, *, client=None, allow_empty=False):
        assert allow_empty
        return [dict(item) for key, rows in weeks.items() if key in url for item in rows]

    monkeypatch.setattr(usc_source, "fetch_course", fake_fetch)
    result = sync(
        config_path=config_path,
        db_path=tmp_path / "sync.db",
        csv_path=tmp_path / "sync.csv",
        catalog_path=tmp_path / "catalog.json",
    )
    assert result["mode"] == "live"
    catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    assert [week["id"] for week in catalog["term"]["calendar"]] == ["2025-W40", "2025-W42"]
    by_group = {session["group_id"].rsplit("-", 1)[-1]: session for session in catalog["class_sessions"]}
    # The same meeting in two weeks is one session, not two.
    assert len(catalog["class_sessions"]) == 2
    assert by_group["cle01"]["weeks"] == ["2025-W40", "2025-W42"]
    assert by_group["clil01"]["weeks"] == ["2025-W42"]


def test_parse_keeps_rooms_without_aula_prefix() -> None:
    html = SAMPLE_HTML.replace("Grupo/CLE_02<br>Aula 06", "Grupo/CLE_02<br>IA.S1").replace(
        "Grupo/CLIL_02<br>Aula 09", "Grupo/CLIL_02"
    )
    rows = parse_calendar_html(html, "Primero")
    assert rows[0]["room"] == "IA.S1"
    assert rows[1]["room"] == ""


def test_rooms_map_to_their_building() -> None:
    from backend.usc_source import building_for_room, building_rules, building_short_names, load_config

    config = load_config()
    rules = building_rules(config)
    etse = "Escuela Técnica Superior de Ingeniería"
    assert building_for_room("IA.01", etse, rules) == "Edificio Emprendia"
    assert building_for_room("LB 5", etse, rules) == etse
    assert building_for_room("Aula A1", etse, rules) == etse
    physics = "Facultad de Física"
    for room in ("Aula B", "Aula C", "Aula D", "Laboratorio de Instrumentación 2"):
        assert building_for_room(room, physics, rules) == "Colexio da Condesa"
    assert building_for_room("Aula Magna", physics, rules) == physics
    assert building_for_room("Aula BC", physics, rules) == physics
    short = building_short_names(config)
    assert {short[name] for name in short} == {"Matemáticas", "Física", "ETSE", "Emprendia", "Condesa"}
