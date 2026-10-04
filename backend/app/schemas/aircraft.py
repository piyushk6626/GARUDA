"""Validated contracts for the fictional aircraft availability domain."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIRCRAFT_EVENT_NAMESPACE = uuid.UUID("bc6bc4ec-0f7a-56d9-b4d4-40c49b9ec8a0")
SCHEMA_VERSION = 1


class AircraftStatus(StrEnum):
    FMC = "FMC"
    PMC = "PMC"
    NMC = "NMC"
    AOG = "AOG"
    IN_MISSION = "IN_MISSION"


CANONICAL_TYPES = ("Su-30MKI", "Rafale", "Tejas", "Mirage-2000", "Jaguar", "C-130J", "IL-78", "AEW&C", "helicopter")
TYPE_ALIASES = {"AEW": "AEW&C", "AWACS": "AEW&C", "Heli": "helicopter", "HELI": "helicopter"}


def canonical_type(value: str) -> str:
    value = str(value).strip()
    value = TYPE_ALIASES.get(value, value)
    if value not in CANONICAL_TYPES:
        raise ValueError(f"unknown aircraft type: {value}")
    return value


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class AircraftType(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    fuel_capacity_kg: float = Field(gt=0)
    inspection_cycle_hours: float = Field(gt=0)
    turnaround_min: int = Field(gt=0)
    range_km: float = Field(gt=0)
    cruise_kts: float = Field(gt=0)
    ceiling_ft: float = Field(gt=0)
    capabilities: tuple[str, ...] = ()

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        return canonical_type(value)


class AircraftState(BaseModel):
    """Full immutable snapshot emitted in every aircraft event."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    tail_no: str = Field(min_length=1, max_length=64, pattern=r"^[A-Z0-9-]+$")
    run_id: str = Field(min_length=1, max_length=64)
    type: str
    sqn_id: str = Field(min_length=1, max_length=64)
    base_id: str = Field(min_length=1, max_length=64)
    status: AircraftStatus
    status_reason: str | None = None
    fuel_kg: float = Field(ge=0)
    flight_hours_total: float = Field(ge=0)
    hours_since_inspection: float = Field(ge=0)
    hours_to_next_inspection: float = Field(ge=0)
    maint_return_eta: datetime | None = None
    capabilities: tuple[str, ...] = ()
    turnaround_min: int = Field(gt=0)
    status_since: datetime
    ready_at: datetime
    updated_at: datetime
    version: int = Field(ge=1)
    last_event_id: uuid.UUID

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        return canonical_type(value)

    @field_validator("status_since", "ready_at", "updated_at", "maint_return_eta")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else utc(value)

    @field_validator("fuel_kg", "flight_hours_total", "hours_since_inspection", "hours_to_next_inspection")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("numeric values must be finite")
        return value

    @model_validator(mode="after")
    def _state_rules(self) -> "AircraftState":
        if self.status is AircraftStatus.FMC and (self.maint_return_eta or self.status_reason):
            raise ValueError("FMC cannot have a maintenance ETA or fault reason")
        if self.status in (AircraftStatus.NMC, AircraftStatus.AOG) and not self.status_reason:
            raise ValueError("NMC/AOG require a reason")
        if self.maint_return_eta and self.maint_return_eta <= self.updated_at:
            raise ValueError("maintenance ETA must be after event time")
        return self


class AircraftCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tail_no: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    event_time: datetime
    kind: Literal["seed", "status", "fuel", "sortie_start", "sortie_end", "inspection", "repair", "transfer", "injection"]
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_version: int | None = Field(default=None, ge=1)
    status: AircraftStatus | None = None
    reason: str | None = None
    fuel_kg: float | None = Field(default=None, ge=0)
    base_id: str | None = Field(default=None, min_length=1)
    maint_return_eta: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_time", "maint_return_eta")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else utc(value)


class AircraftEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: uuid.UUID
    type: Literal["aircraft.availability.updated"] = "aircraft.availability.updated"
    entity_id: str
    ts: datetime
    version: int = Field(ge=1)
    payload: dict[str, Any]

    @field_validator("ts")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return utc(value)

    @classmethod
    def from_state(cls, state: AircraftState, *, kind: str, prior_status: AircraftStatus | None,
                   metadata: dict[str, Any] | None = None) -> "AircraftEvent":
        event_id = uuid.uuid5(AIRCRAFT_EVENT_NAMESPACE, f"{state.run_id}:{state.tail_no}:{state.version}")
        payload = {"schema_version": SCHEMA_VERSION, "run_id": state.run_id,
                   "snapshot": state.model_dump(mode="json"), "kind": kind,
                   "prior_status": prior_status.value if prior_status else None,
                   "metadata": metadata or {}}
        return cls(event_id=event_id, entity_id=state.tail_no, ts=state.updated_at,
                   version=state.version, payload=payload)

    def state(self) -> AircraftState:
        return AircraftState.model_validate(self.payload["snapshot"])


class AvailabilityQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    base_id: str | None = None
    type: str | None = None
    status: AircraftStatus | None = None
    capabilities: tuple[str, ...] = ()
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None

    @field_validator("type")
    @classmethod
    def _type(cls, value: str | None) -> str | None:
        return None if value is None else canonical_type(value)
