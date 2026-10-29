"""Request bodies of the JSON API.

Every field has limits (``max_length``, ``ge``/``le``) so a request cannot
make the server do unbounded work.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import CustomEvent, Day
from .timeutil import parse_hm


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


def blocked_dicts(payload: list[BlockedSlot]) -> list[dict]:
    return [slot.as_dict() for slot in payload]


def event_dicts(payload: list[CustomEvent]) -> list[dict]:
    return [event.model_dump(mode="json") for event in payload]
