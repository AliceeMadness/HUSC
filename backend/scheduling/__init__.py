"""Conflict detection, validation, free time, the generator and the repair.

All algorithms operate on the canonical catalogue and return plain JSON-ready
dictionaries.

    common.py      input normalisation, group lookups, the overlap rule
    views.py       conflicts, free time and merged blocks of session views
    validation.py  validate_selection, free_slots_for_groups
    variables.py   search model shared by generator and repair
    generator.py   generate_schedule (API only; the UI repairs instead)
    repair.py      optimize_schedule ("Resolver solapes")
"""

from __future__ import annotations

from .common import (
    DAY_ORDER,
    DAYS,
    in_week,
    normalize_blocked,
    normalize_events,
    resolve_groups,
    sessions_for_groups,
    share_week,
)
from .generator import generate_schedule
from .repair import optimize_schedule
from .validation import free_slots_for_groups, validate_selection
from .variables import Variable, _quality
from .views import find_conflicts, free_intervals, merge_session_views

__all__ = [
    "DAYS",
    "DAY_ORDER",
    "Variable",
    "_quality",
    "find_conflicts",
    "free_intervals",
    "free_slots_for_groups",
    "generate_schedule",
    "in_week",
    "merge_session_views",
    "normalize_blocked",
    "normalize_events",
    "optimize_schedule",
    "resolve_groups",
    "sessions_for_groups",
    "share_week",
    "validate_selection",
]
