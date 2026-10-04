from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AirspaceType = Literal[
    "PROHIBITED",
    "RESTRICTED",
    "DANGER",
    "CTR",
    "TMA",
    "ADIZ",
    "CAP",
    "AAR_TRACK",
    "CORRIDOR",
    "TRA",
]
AirspaceSource = Literal["openaip", "synthetic"]


class AirspaceZone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: AirspaceType
    wkt: str = Field(min_length=1)
    floor_ft: int
    ceiling_ft: int
    active_from: datetime | None = None
    active_to: datetime | None = None
    controlling_authority: str = Field(min_length=1)
    source: AirspaceSource
    capacity_per_hour: int | None = Field(default=None, ge=0)
    width_km: float | None = Field(default=None, gt=0)
    mission_id: str | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> "AirspaceZone":
        if self.floor_ft > self.ceiling_ft:
            raise ValueError("floor_ft is above ceiling_ft")
        if (self.active_from is None) != (self.active_to is None):
            raise ValueError("a scheduled zone needs both active_from and active_to")
        if self.active_from is not None and self.active_to is not None:
            if self.active_from.tzinfo is None or self.active_to.tzinfo is None:
                raise ValueError("activation bounds must be timezone-aware")
            if self.active_from >= self.active_to:
                raise ValueError("active_from must be before active_to")
        return self


def is_active(zone: AirspaceZone, at: datetime) -> bool:
    """Permanent zones apply at every instant. Scheduled windows are half-open."""
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    if zone.active_from is None or zone.active_to is None:
        return True
    return zone.active_from <= at < zone.active_to
