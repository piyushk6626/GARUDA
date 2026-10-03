from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ThreatType = Literal[
    "SAM_LONG",
    "SAM_MED",
    "SAM_SHORT",
    "RADAR_EW",
    "RADAR_GCI",
    "AAA",
    "ENEMY_CAP",
    "AIRFIELD",
]
ThreatStatus = Literal["ACTIVE", "SUPPRESSED", "DESTROYED", "UNKNOWN"]


class Threat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threat_id: str
    type: ThreatType
    lon: float
    lat: float
    mobile: bool
    engagement_range_km: float = Field(ge=0)
    detection_range_km: float = Field(ge=0)
    min_alt_ft: int
    max_alt_ft: int
    pk_base: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    first_seen: datetime
    last_seen: datetime
    status: ThreatStatus

    @model_validator(mode="after")
    def check_envelope(self) -> "Threat":
        if self.detection_range_km < self.engagement_range_km:
            raise ValueError("detection_range_km must cover engagement_range_km")
        if self.max_alt_ft < self.min_alt_ft:
            raise ValueError("max_alt_ft must be at least min_alt_ft")
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen must be at or after first_seen")
        return self


class ThreatTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    threat_id: str
    lon: float
    lat: float
    speed_kts: float = Field(gt=0)
    heading: float = Field(ge=0, lt=360)
