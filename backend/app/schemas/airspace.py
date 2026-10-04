"""Strict, portable contracts for the airspace system of record."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AIRSPACE_EVENT_NAMESPACE = uuid.UUID("15885259-3ff7-5d3f-8d09-7bf8a3fb2c13")
SCHEMA_VERSION = 1


class AirspaceType(StrEnum):
    PROHIBITED = "PROHIBITED"; RESTRICTED = "RESTRICTED"; DANGER = "DANGER"
    CTR = "CTR"; TMA = "TMA"; ADIZ = "ADIZ"; CAP = "CAP"; AAR_TRACK = "AAR_TRACK"
    CORRIDOR = "CORRIDOR"; TRA = "TRA"


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


class AltitudeLimit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    value: float | None = None
    unit: Literal["ft", "m", "surface", "unlimited", "fl", "agl", "unknown"] = "unknown"
    datum: Literal["AMSL", "AGL", "pressure", "unknown"] = "unknown"

    @model_validator(mode="after")
    def check(self) -> "AltitudeLimit":
        if self.value is not None and (not math.isfinite(self.value) or self.value < 0):
            raise ValueError("altitude must be finite and non-negative")
        if self.unit in {"ft", "m"} and self.value is None:
            raise ValueError("numeric altitude unit requires a value")
        return self

    def amsl_ft(self) -> tuple[int | None, str | None]:
        if self.unit == "ft" and self.datum == "AMSL": return round(self.value or 0), None
        if self.unit == "m" and self.datum == "AMSL": return round((self.value or 0) * 3.280839895), None
        return None, f"{self.datum}:{self.unit} requires a conversion adapter"


class ActivationWindow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    start: datetime; end: datetime; mission_id: str | None = None
    version: int = Field(default=1, ge=1)

    @field_validator("start", "end")
    @classmethod
    def valid_time(cls, value: datetime) -> datetime: return utc(value)

    @model_validator(mode="after")
    def ordered(self) -> "ActivationWindow":
        if self.start >= self.end: raise ValueError("activation windows are [start,end) and start must precede end")
        return self


class AirspaceZone(BaseModel):
    """Geometry is canonical GeoJSON in EPSG:4326; corridors keep a buffered footprint."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    zone_id: str = Field(pattern=r"^[A-Za-z0-9:_-]+$", max_length=160)
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)
    name: str = Field(min_length=1, max_length=256)
    type: AirspaceType
    geometry: dict[str, Any]
    floor: AltitudeLimit = Field(default_factory=AltitudeLimit)
    ceiling: AltitudeLimit = Field(default_factory=AltitudeLimit)
    controlling_authority: str = Field(min_length=1, max_length=128)
    source: Literal["synthetic", "openaip", "fabricated"]
    capacity_per_hour: int | None = Field(default=None, ge=0)
    width_km: float | None = Field(default=None, gt=0)
    centerline: dict[str, Any] | None = None
    windows: tuple[ActivationWindow, ...] = ()
    enabled: bool = True
    version: int = Field(default=1, ge=1)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry")
    @classmethod
    def polygon(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") not in {"Polygon", "MultiPolygon"}: raise ValueError("geometry must be Polygon or MultiPolygon")
        if not value.get("coordinates"): raise ValueError("geometry must not be empty")
        return value

    @model_validator(mode="after")
    def altitude_order(self) -> "AirspaceZone":
        floor, floor_reason = self.floor.amsl_ft(); ceiling, ceiling_reason = self.ceiling.amsl_ft()
        if floor is not None and ceiling is not None and floor > ceiling: raise ValueError("floor cannot exceed ceiling")
        if self.type in {AirspaceType.CORRIDOR, AirspaceType.AAR_TRACK} and self.width_km is None:
            raise ValueError("corridors and tracks require a positive width_km")
        return self

    @property
    def floor_ft(self) -> int | None: return self.floor.amsl_ft()[0]
    @property
    def ceiling_ft(self) -> int | None: return self.ceiling.amsl_ft()[0]
    @property
    def altitude_reason(self) -> str | None: return self.floor.amsl_ft()[1] or self.ceiling.amsl_ft()[1]


class AirspaceEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: uuid.UUID; type: Literal["airspace.activation"] = "airspace.activation"
    entity_id: str; ts: datetime; version: int = Field(ge=1); payload: dict[str, Any]

    @field_validator("ts")
    @classmethod
    def valid_time(cls, value: datetime) -> datetime: return utc(value)

    @classmethod
    def from_zone(cls, zone: AirspaceZone, *, at: datetime, active: bool, kind: str = "updated") -> "AirspaceEvent":
        at = utc(at); event_id = uuid.uuid5(AIRSPACE_EVENT_NAMESPACE, f"{zone.run_id}:{zone.zone_id}:{zone.version}")
        return cls(event_id=event_id, entity_id=zone.zone_id, ts=at, version=zone.version,
                   payload={"schema_version": SCHEMA_VERSION, "run_id": zone.run_id, "kind": kind,
                            "active": active, "effective_clock": at.isoformat(),
                            "snapshot": zone.model_dump(mode="json")})

    def zone(self) -> AirspaceZone: return AirspaceZone.model_validate(self.payload["snapshot"])
