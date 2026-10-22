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
