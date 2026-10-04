"""Input/output models from docs/03_optimization_layer/weather_impact_prediction.md."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from garuda.contracts.geo import LatLon, TimeWindow


class AlternateBase(BaseModel):
    base_id: str
    p_go: float


class PredictWeatherImpactInput(BaseModel):
    mission_id: str | None = None
    route: list[LatLon] | None = None
    launch_base: str
    recovery_base: str | None = None
    start_time: datetime
    aircraft_type: str
    weapon_guidance: Literal["LASER", "GPS", "IR", "NONE"] = "NONE"


class PredictWeatherImpactOutput(BaseModel):
    p_go: float
    p_degraded: float
    p_no_go: float
    limiting_factors: list[str]
    better_windows: list[TimeWindow]
    alternates: list[AlternateBase]
