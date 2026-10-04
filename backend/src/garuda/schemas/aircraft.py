from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AircraftTypeName = Literal[
    "Su-30MKI",
    "Rafale",
    "Tejas",
    "Mirage-2000",
    "Jaguar",
    "C-130J",
    "IL-78",
    "AEW&C",
    "helicopter",
]
AircraftStatus = Literal["FMC", "PMC", "NMC", "AOG", "IN_MISSION"]
FLEET_TYPES: tuple[str, ...] = (
    "Su-30MKI",
    "Rafale",
    "Tejas",
    "Mirage-2000",
    "Jaguar",
    "C-130J",
    "IL-78",
    "AEW&C",
    "helicopter",
)
TYPE_ALIASES = {"AEW": "AEW&C", "AWACS": "AEW&C", "Heli": "helicopter"}


def canonical_type(name: str) -> str:
    canonical = TYPE_ALIASES.get(name, name)
    if canonical not in FLEET_TYPES:
        known = ", ".join(FLEET_TYPES)
        raise ValueError(f"unknown aircraft type {name}; valid: {known}")
    return canonical


class AircraftTypeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AircraftTypeName
    fuel_capacity_kg: float = Field(gt=0)
    inspection_cycle_hours: float = Field(gt=0)
    turnaround_min: int = Field(gt=0)
    range_km: float = Field(gt=0)
    cruise_kts: float = Field(gt=0)
    ceiling_ft: float = Field(gt=0)
    capabilities: list[str] = Field(min_length=1)


class Squadron(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sqn_id: str = Field(min_length=1)
    type: AircraftTypeName
    home_base_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class Aircraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tail_no: str = Field(min_length=1)
    type: AircraftTypeName
    sqn_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    status: AircraftStatus
    status_reason: str | None = None
    fuel_kg: float = Field(ge=0)
    fuel_capacity_kg: float = Field(gt=0)
    flight_hours_total: float = Field(ge=0)
    hours_to_next_inspection: float = Field(ge=0)
    inspection_cycle_hours: float = Field(gt=0)
    maint_return_eta: datetime | None = None
    turnaround_min: int = Field(gt=0)
    capabilities: list[str] = Field(min_length=1)
    range_km: float = Field(gt=0)
    cruise_kts: float = Field(gt=0)
    ceiling_ft: float = Field(gt=0)
    updated_at: datetime

    @model_validator(mode="after")
    def check_readiness(self) -> "Aircraft":
        if self.fuel_kg > self.fuel_capacity_kg:
            raise ValueError("fuel_kg exceeds capacity")
        if self.hours_to_next_inspection > self.inspection_cycle_hours:
            raise ValueError("hours_to_next_inspection exceeds the inspection cycle")
        if self.flight_hours_total + 1e-6 < self.inspection_cycle_hours - self.hours_to_next_inspection:
            raise ValueError("flight hours cannot be less than hours since inspection")
        _require_aware(self.updated_at, "updated_at")
        if self.maint_return_eta is not None:
            _require_aware(self.maint_return_eta, "maint_return_eta")
            if self.maint_return_eta <= self.updated_at:
                raise ValueError("maint_return_eta must be after updated_at")
        if self.status == "FMC" and (self.status_reason is not None or self.maint_return_eta is not None):
            raise ValueError("FMC aircraft have no fault reason and no maintenance ETA")
        if self.status in {"PMC", "NMC", "AOG"} and not self.status_reason:
            raise ValueError(f"{self.status} requires a status reason")
        if self.status in {"NMC", "AOG"} and self.maint_return_eta is None:
            raise ValueError(f"{self.status} requires a maintenance ETA")
        return self


class AircraftHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    tail_no: str
    status: AircraftStatus
    base_id: str
    fuel_kg: float = Field(ge=0)
    flight_hours_total: float = Field(ge=0)
    hours_to_next_inspection: float = Field(ge=0)


def _require_aware(moment: datetime, field: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
