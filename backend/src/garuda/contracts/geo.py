"""Geometry and time primitives shared by every tool."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LatLon(BaseModel):
    model_config = ConfigDict(frozen=True)

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PolygonModel(BaseModel):
    """Closed ring of (lat, lon) vertices."""

    ring: list[tuple[float, float]] = Field(min_length=3)


class TimeWindow(BaseModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def check_order(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class Waypoint(BaseModel):
    lat: float
    lon: float
    alt_ft: int = Field(ge=0)
    eta: datetime
