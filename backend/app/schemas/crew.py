"""Contracts for fictional, pseudonymous crew availability data."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .aircraft import canonical_type

CREW_EVENT_NAMESPACE = uuid.UUID("df778797-0a8e-542c-a58b-36253f0b248b")
SCHEMA_VERSION = 1


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class CrewRole(StrEnum):
    PILOT = "PILOT"
    WSO = "WSO"
    LOADMASTER = "LOADMASTER"
    AEW_OP = "AEW_OP"


class MedicalStatus(StrEnum):
    FIT = "FIT"
    LIMITED = "LIMITED"
    GROUNDED = "GROUNDED"


class DutyKind(StrEnum):
    DUTY = "duty"
    REST = "rest"
    SLEEP = "sleep"
    FLIGHT = "flight"


class Currency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    qualification: dict[str, datetime | None] = Field(default_factory=dict)
    night: datetime | None = None
    aar: datetime | None = None

    @field_validator("qualification")
    @classmethod
    def types(cls, value: dict[str, datetime | None]) -> dict[str, datetime | None]:
        return {canonical_type(name): (utc(date) if date else None) for name, date in value.items()}

    @field_validator("night", "aar")
    @classmethod
    def dates(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None


class DutyInterval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    interval_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    crew_id: str
    run_id: str
    start_at: datetime
    end_at: datetime | None = None
    kind: DutyKind
    mission_id: str | None = None
    qualification: str | None = None
    night: bool = False
    aar: bool = False

    @field_validator("start_at", "end_at")
    @classmethod
    def times(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None

    @model_validator(mode="after")
    def bounds(self) -> "DutyInterval":
        if self.end_at and self.end_at <= self.start_at:
            raise ValueError("interval end must be after start")
        if self.kind is not DutyKind.FLIGHT and (self.night or self.aar or self.qualification):
            raise ValueError("flight attributes require a flight interval")
        return self


class CrewState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)
    crew_id: str = Field(min_length=1, max_length=96)
    run_id: str = Field(min_length=1, max_length=64)
    callsign: str = Field(min_length=1, max_length=64)
    role: CrewRole
    sqn_id: str
    base_id: str
    primary_type: str
    qualifications: dict[str, tuple[str, ...]]
    currency: Currency
    flight_hours_total: float = Field(ge=0)
    flight_hours_last_30d: float = Field(default=0, ge=0, validation_alias="flight_hours_30d")
    duty_hours_24h: float = Field(default=0, ge=0, le=24)
    rest_hours_since_last_duty: float = Field(default=0, ge=0, validation_alias="rest_hours")
    fatigue_score: float = Field(ge=0, le=1)
    medical_status: MedicalStatus
    leave_until: datetime | None = None
    current_assignment: str | None = None
    is_flight_lead: bool = False
    logical_time: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    last_event_id: uuid.UUID

    @field_validator("primary_type")
    @classmethod
    def primary(cls, value: str) -> str:
        return canonical_type(value)

    @field_validator("qualifications")
    @classmethod
    def quals(cls, value: dict[str, tuple[str, ...] | list[str]]) -> dict[str, tuple[str, ...]]:
        if not value:
            raise ValueError("at least one aircraft qualification is required")
        result: dict[str, tuple[str, ...]] = {}
        for kind, entries in value.items():
            result[canonical_type(kind)] = tuple(sorted(set(entries)))
        return result

    @field_validator("leave_until", "logical_time", "updated_at")
    @classmethod
    def times(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None

    @field_validator("flight_hours_total", "flight_hours_last_30d", "duty_hours_24h", "rest_hours_since_last_duty", "fatigue_score")
    @classmethod
    def finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("numeric values must be finite")
        return value

    @model_validator(mode="after")
    def valid_state(self) -> "CrewState":
        if self.primary_type not in self.qualifications:
            raise ValueError("primary type must be qualified")
        if self.flight_hours_last_30d > self.flight_hours_total:
            raise ValueError("recent hours cannot exceed lifetime hours")
        for date in (*self.currency.qualification.values(), self.currency.night, self.currency.aar):
            if date and date > self.logical_time:
                raise ValueError("last-flown date cannot be future of snapshot")
        return self


class CrewCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    crew_id: str
    run_id: str
    event_time: datetime
    kind: Literal["seed", "duty_start", "duty_end", "sortie_start", "sortie_end", "rest", "sleep", "transfer", "qualification", "medical", "leave", "tick", "injection"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    interval: DutyInterval | None = None
    base_id: str | None = None
    current_assignment: str | None = None
    medical_status: MedicalStatus | None = None
    leave_until: datetime | None = None
    qualifications: dict[str, tuple[str, ...]] | None = None
    currency: Currency | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_time", "leave_until")
    @classmethod
    def times(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value else None


class CrewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: uuid.UUID
    type: Literal["crew.status.changed"] = "crew.status.changed"
    entity_id: str
    ts: datetime
    version: int = Field(ge=1)
    payload: dict[str, Any]

    @field_validator("ts")
    @classmethod
    def time(cls, value: datetime) -> datetime:
        return utc(value)

    @classmethod
    def from_state(cls, state: CrewState, *, kind: str, intervals: tuple[DutyInterval, ...] = (), policy_reasons: tuple[str, ...] = ()) -> "CrewEvent":
        event_id = uuid.uuid5(CREW_EVENT_NAMESPACE, f"{state.run_id}:{state.crew_id}:{state.version}")
        return cls(event_id=event_id, entity_id=state.crew_id, ts=state.logical_time, version=state.version,
            payload={"schema_version": SCHEMA_VERSION, "run_id": state.run_id, "snapshot": state.model_dump(mode="json"),
                     "kind": kind, "intervals": [item.model_dump(mode="json") for item in intervals],
                     "model_version": "crew-fatigue-v1", "policy_reasons": list(policy_reasons)})

    def state(self) -> CrewState:
        return CrewState.model_validate(self.payload["snapshot"])


class CrewQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    base_id: str | None = None
    role: CrewRole | None = None
    medical_status: MedicalStatus | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None


class CrewAvailabilityQuery(CrewQuery):
    aircraft_type: str
    required_role: CrewRole
    at: datetime
    slot_end: datetime
    qualification: str | None = None
    require_night: bool = False
    require_aar: bool = False

    @field_validator("aircraft_type")
    @classmethod
    def type(cls, value: str) -> str:
        return canonical_type(value)

    @field_validator("at", "slot_end")
    @classmethod
    def time(cls, value: datetime) -> datetime:
        return utc(value)

    @model_validator(mode="after")
    def window(self) -> "CrewAvailabilityQuery":
        if self.slot_end <= self.at:
            raise ValueError("slot end must be after evaluation time")
        return self
