from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from garuda.schemas.aircraft import AircraftTypeName

CrewRole = Literal["PILOT", "WSO", "LOADMASTER", "AEW_OP"]
MedicalStatus = Literal["FIT", "LIMITED", "GROUNDED"]
DUTY_LIMIT_HOURS = 14.0
REST_MINIMUM_HOURS = 12.0
STORAGE_DUTY_LIMIT_HOURS = 24.0


class Currency(BaseModel):
    model_config = ConfigDict(extra="forbid")

    by_type: dict[str, datetime]
    night: datetime | None = None
    aar: datetime | None = None

    @model_validator(mode="after")
    def check_dates(self) -> "Currency":
        for name, moment in self.by_type.items():
            if not name:
                raise ValueError("currency type name is empty")
            _require_aware(moment, "currency.by_type")
        if self.night is not None:
            _require_aware(self.night, "currency.night")
        if self.aar is not None:
            _require_aware(self.aar, "currency.aar")
        return self


class Crew(BaseModel):
    model_config = ConfigDict(extra="forbid")

    crew_id: str = Field(min_length=1)
    callsign: str = Field(min_length=1)
    role: CrewRole
    sqn_id: str = Field(min_length=1)
    base_id: str = Field(min_length=1)
    primary_type: AircraftTypeName
    qualifications: dict[str, list[str]]
    currency: Currency
    flight_hours_total: float = Field(ge=0)
    flight_hours_last_30d: float = Field(ge=0)
    duty_hours_24h: float = Field(ge=0, le=STORAGE_DUTY_LIMIT_HOURS)
    rest_hours_since_last_duty: float = Field(ge=0)
    fatigue_score: float = Field(ge=0, le=1)
    medical_status: MedicalStatus
    leave_until: datetime | None = None
    current_assignment: str | None = None
    is_flight_lead: bool = False
    updated_at: datetime

    @model_validator(mode="after")
    def check_roster_rules(self) -> "Crew":
        _require_aware(self.updated_at, "updated_at")
        if self.leave_until is not None:
            _require_aware(self.leave_until, "leave_until")
        if self.flight_hours_last_30d > self.flight_hours_total + 1e-6:
            raise ValueError("flight_hours_last_30d exceeds flight_hours_total")
        if self.primary_type not in self.qualifications:
            raise ValueError("primary type is missing from qualifications")
        if any(not quals for quals in self.qualifications.values()):
            raise ValueError("each qualification entry needs at least one mission qualification")
        if self.duty_hours_24h > 0 and self.rest_hours_since_last_duty != 0:
            raise ValueError("rest is zero while on duty")
        if self.duty_hours_24h == 0 and self.rest_hours_since_last_duty < REST_MINIMUM_HOURS:
            raise ValueError("off-duty rest is below the 12 hour minimum")
        for moment in self.currency.by_type.values():
            if moment > self.updated_at:
                raise ValueError("last-flown date is after the snapshot")
        if self.currency.night is not None and self.currency.night > self.updated_at:
            raise ValueError("night currency is after the snapshot")
        if self.currency.aar is not None and self.currency.aar > self.updated_at:
            raise ValueError("AAR currency is after the snapshot")
        return self


class CrewDutyLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    crew_id: str
    duty_hours_24h: float = Field(ge=0, le=STORAGE_DUTY_LIMIT_HOURS)
    fatigue_score: float = Field(ge=0, le=1)


def _require_aware(moment: datetime, field: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
