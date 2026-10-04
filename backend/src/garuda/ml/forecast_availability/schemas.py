"""Input/output models from docs/03_optimization_layer/aircraft_availability_forecast.md."""

from pydantic import BaseModel, Field


class AircraftForecast(BaseModel):
    tail: str
    prob_by_horizon: dict[str, float]
    top_drivers: list[str]


class FleetForecast(BaseModel):
    mean: float
    p10: float
    p90: float


class ForecastAvailabilityInput(BaseModel):
    base_ids: list[str] | None = None
    types: list[str] | None = None
    horizons_h: list[int] = Field(default_factory=lambda: [24, 48, 72])
    include_planned_sorties_from: str | None = None


class ForecastAvailabilityOutput(BaseModel):
    per_aircraft: list[AircraftForecast]
    fleet: dict[str, FleetForecast]
    model_version: str
