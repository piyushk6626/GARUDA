from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from garuda.schemas.threat import Threat, ThreatTrack
from garuda.schemas.weapon import Compatibility, LoadConfig, WeaponStock, WeaponType

TargetClass = Literal["RUNWAY", "RADAR", "BRIDGE", "ARMOUR", "C2", "FUEL_DEPOT"]
MissionType = Literal["DCA", "CAP", "STRIKE", "SEAD", "CAS", "ISR", "AAR", "AIRLIFT", "SAR"]
MissionEffect = Literal["DESTROY", "SUPPRESS", "OBSERVE", "DENY"]
MissionStatus = Literal[
    "REQUESTED",
    "PLANNED",
    "APPROVED",
    "EXECUTING",
    "DONE",
    "CANCELLED",
]


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    target_class: TargetClass
    lon: float
    lat: float
    value: float
    hardness: float = Field(ge=0, le=1)
    defended_by: list[str]

    @property
    def wkt(self) -> str:
        return f"POINT({self.lon} {self.lat})"


class Mission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mission_id: str
    type: MissionType
    priority: int = Field(ge=1, le=5)
    value: float
    requested_by: str
    window_start: datetime
    window_end: datetime
    duration_min: int = Field(gt=0)
    target_id: str | None
    area_wkt: str | None
    required_capabilities: list[str]
    min_aircraft: int = Field(gt=0)
    max_aircraft: int = Field(gt=0)
    required_effect: MissionEffect
    min_pk: float = Field(ge=0, le=1)
    status: MissionStatus = "REQUESTED"
    dependencies: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_window_target_and_duration(self) -> "Mission":
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.type in {"CAP", "AAR"} and self.target_id is not None:
            raise ValueError("CAP and AAR missions have no target")
        window_min = int((self.window_end - self.window_start).total_seconds() // 60)
        if self.duration_min > window_min:
            raise ValueError("duration_min exceeds the time window")
        return self


class GeneratedScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int
    targets: list[Target]
    missions: list[Mission]
    threats: list[Threat]
    tracks: list[ThreatTrack]
    weapons: list[WeaponType]
    stocks: list[WeaponStock]
    load_configs: list[LoadConfig]
    compatibilities: list[Compatibility]
