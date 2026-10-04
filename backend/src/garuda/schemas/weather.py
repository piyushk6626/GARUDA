from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WeatherSource = Literal["synthetic", "open-meteo", "metar"]
FlightCategory = Literal["VFR", "MVFR", "IFR", "LIFR"]


class WeatherCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    lat: float
    lon: float
    cloud_low_pct: float = Field(ge=0, le=100)
    cloud_mid_pct: float = Field(ge=0, le=100)
    cloud_high_pct: float = Field(ge=0, le=100)
    ceiling_ft: float | None = Field(default=None, ge=0)
    visibility_km: float = Field(ge=0)
    wind_kts: float = Field(ge=0)
    wind_dir_deg: float = Field(ge=0, lt=360)
    gust_kts: float = Field(ge=0)
    precip_mm_h: float = Field(ge=0)
    cape_jkg: float = Field(ge=0)
    temp_c: float
    freezing_level_ft: float = Field(ge=0)
    source: WeatherSource
    issued_at: datetime
    flyability: float = Field(ge=0, le=1)
    cb_flag: bool

    @model_validator(mode="after")
    def check_derived_fields(self) -> "WeatherCell":
        if self.cb_flag != (self.cape_jkg > 1000):
            raise ValueError("cb_flag must match cape_jkg > 1000")
        if self.cloud_low_pct < 50 and self.ceiling_ft is not None:
            raise ValueError("ceiling is unset when low cloud is below 50 percent")
        if self.cloud_low_pct >= 50 and self.ceiling_ft is None:
            raise ValueError("ceiling is required when low cloud is at least 50 percent")
        return self


class WeatherStationObs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    icao: str
    obs_time: datetime
    raw_text: str
    flight_category: FlightCategory
    ceiling_ft: float | None = Field(default=None, ge=0)
    vis_km: float = Field(ge=0)
    wind_kts: float = Field(ge=0)
    crosswind_kts: float = Field(ge=0)
    flyability: float = Field(ge=0, le=1)
    source: WeatherSource


class WeatherRouteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route: list[tuple[float, float]] = Field(min_length=1)
    start_time: datetime
    speed_kts: float = Field(gt=0)
    altitude_ft: int


class WeatherSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: datetime
    lat: float
    lon: float
    flyability: float = Field(ge=0, le=1)
    ceiling_ft: float | None = None
    vis_km: float
    wind_kts: float
    wind_dir_deg: float
    cb_flag: bool


class WeatherRouteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segments: list[WeatherSegment]
    worst_flyability: float = Field(ge=0, le=1)
    advisories: list[str]
