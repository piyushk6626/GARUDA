"""Strict, source-shaped ADS-B contracts and OpenSky normalization."""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_NAMESPACE = uuid.UUID("6e44cd1e-7c12-5c8e-a39c-e74f8d5de2fc")
SCHEMA_VERSION = 1


def utc_from_epoch(value: int | float | None) -> datetime | None:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


class PositionObservation(BaseModel):
    """One normalized, observed point; no claim that it is civil traffic."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    icao24: str = Field(pattern=r"^[0-9a-f]{6}$")
    time: datetime
    source_id: str
    callsign: str | None = None
    origin_country: str | None = None
    last_contact: datetime | None = None
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)
    alt_m: float | None = None
    geo_alt_m: float | None = None
    speed_ms: float | None = Field(default=None, ge=0)
    heading: float | None = Field(default=None, ge=0, lt=360)
    vrate: float | None = None
    on_ground: bool
    position_source: int | None = None
    response_time: datetime | None = None
    ingested_at: datetime
    snapshot_key: str | None = None
    original_position_time: datetime | None = None
    replay_id: str | None = None

    @field_validator("icao24", mode="before")
    @classmethod
    def canonical_icao(cls, value: Any) -> str:
        return str(value).lower().strip()

    @field_validator("callsign", "origin_country", mode="before")
    @classmethod
    def blank_is_null(cls, value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @field_validator("time", "last_contact", "response_time", "ingested_at", "original_position_time")
    @classmethod
    def utc_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("alt_m", "geo_alt_m", "speed_ms", "heading", "vrate", "lon", "lat")
    @classmethod
    def finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("numeric fields must be finite")
        return value

    @property
    def event_id(self) -> uuid.UUID:
        return uuid.uuid5(EVENT_NAMESPACE, f"{self.source_id}:{self.icao24}:{int(self.time.timestamp())}")

    @property
    def version(self) -> int:
        return int(self.time.timestamp())

    def to_event(self) -> "EventEnvelope":
        return EventEnvelope(
            event_id=self.event_id,
            type="adsb.position.observed",
            entity_id=self.icao24,
            ts=self.time,
            version=self.version,
            payload=self.model_dump(mode="json"),
        )


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: uuid.UUID
    type: Literal["adsb.position.observed"]
    entity_id: str
    ts: datetime
    version: int
    payload: dict[str, Any]

    def observation(self) -> PositionObservation:
        return PositionObservation.model_validate(self.payload)


class NormalizationResult(BaseModel):
    observations: list[PositionObservation]
    malformed_rows: int = 0
    excluded_rows: int = 0
    response_time: datetime | None = None


def _at(row: list[Any], index: int) -> Any:
    return row[index] if len(row) > index else None


def normalize_opensky_response(
    document: dict[str, Any], *, source_id: str, ingested_at: datetime,
    snapshot_key: str | None = None, replay_id: str | None = None,
) -> NormalizationResult:
    """Accept valid neighbours even when OpenSky includes malformed state rows."""
    response_time = utc_from_epoch(document.get("time"))
    states = document.get("states")
    if states is None:
        return NormalizationResult(response_time=response_time)
    if not isinstance(states, list):
        raise ValueError("OpenSky states must be a list or null")
    observations: list[PositionObservation] = []
    malformed = excluded = 0
    for raw in states:
        if not isinstance(raw, (list, tuple)):
            malformed += 1
            continue
        row = list(raw)
        position_time, lon, lat = utc_from_epoch(_at(row, 3)), _at(row, 5), _at(row, 6)
        if position_time is None or lon is None or lat is None:
            excluded += 1
            continue
        try:
            observations.append(PositionObservation(
                icao24=_at(row, 0), callsign=_at(row, 1), origin_country=_at(row, 2),
                time=position_time, last_contact=utc_from_epoch(_at(row, 4)), lon=lon, lat=lat,
                alt_m=_at(row, 7), on_ground=bool(_at(row, 8)), speed_ms=_at(row, 9),
                heading=_at(row, 10), vrate=_at(row, 11), geo_alt_m=_at(row, 13),
                position_source=_at(row, 16), source_id=source_id, response_time=response_time,
                ingested_at=ingested_at, snapshot_key=snapshot_key, replay_id=replay_id,
            ))
        except (TypeError, ValueError):
            malformed += 1
    return NormalizationResult(observations=observations, malformed_rows=malformed,
                               excluded_rows=excluded, response_time=response_time)
