import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

WeaponCategory = Literal[
    "AAM_BVR",
    "AAM_WVR",
    "ASM",
    "LGB",
    "UNGUIDED",
    "ANTI_RADIATION",
    "ANTI_SHIP",
    "PODS",
]
Guidance = Literal["RADAR", "IR", "GPS", "LASER", "OPTICAL", "NONE"]
LoadRole = Literal["DCA", "STRIKE", "SEAD", "CAS", "ISR"]
TARGET_CLASSES = ("RUNWAY", "RADAR", "BRIDGE", "ARMOUR", "C2", "FUEL_DEPOT")
_STATION_LOAD = re.compile(r"^[A-Z0-9-]+x[1-9][0-9]*$")


class WeaponType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weapon_code: str
    category: WeaponCategory
    range_km: float = Field(ge=0)
    weight_kg: float = Field(gt=0)
    pk_by_target_class: dict[str, float]
    min_release_alt_ft: int = Field(ge=0)
    guidance: Guidance

    @model_validator(mode="after")
    def check_pk_matrix(self) -> "WeaponType":
        if set(self.pk_by_target_class) != set(TARGET_CLASSES):
            raise ValueError("pk_by_target_class must cover every target class")
        if any(pk < 0.3 or pk > 0.9 for pk in self.pk_by_target_class.values()):
            raise ValueError("pk must be in [0.3, 0.9]")
        return self


class WeaponStock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str
    weapon_code: str
    quantity: int = Field(ge=0)
    reserved_qty: int = Field(default=0, ge=0)
    updated_at: datetime

    @computed_field
    @property
    def stock_id(self) -> str:
        return f"{self.base_id}:{self.weapon_code}"

    @model_validator(mode="after")
    def check_reserved(self) -> "WeaponStock":
        if self.reserved_qty > self.quantity:
            raise ValueError("reserved_qty exceeds quantity")
        return self


class LoadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_id: str
    aircraft_type: str
    role: LoadRole
    stations: dict[str, list[str]]
    total_weight_kg: float = Field(gt=0)
    range_penalty_pct: float = Field(ge=0, le=15)

    @model_validator(mode="after")
    def check_stations(self) -> "LoadConfig":
        if not self.stations:
            raise ValueError("stations must not be empty")
        for station, loads in self.stations.items():
            if not station or not loads:
                raise ValueError("each station needs a load token")
            if any(_STATION_LOAD.fullmatch(token) is None for token in loads):
                raise ValueError("station load must look like LGB-500x2")
        return self


class Compatibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aircraft_type: str
    weapon_code: str
    allowed_stations: list[str] = Field(min_length=1)
