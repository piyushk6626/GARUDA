"""Input/output models from docs/03_optimization_layer/cpsat_airspace_deconfliction.md."""

from pydantic import BaseModel, Field


class SeparationRules(BaseModel):
    time_min: int = 3
    altitude_ft: int = 1000
    lateral_km: float = 10.0


class FlightAdjustment(BaseModel):
    flight: str
    shift_min: int
    altitude_block: str


class Conflict(BaseModel):
    flight_a: str
    flight_b: str
    zone: str
    detail: str = ""


class DeconflictAirspaceInput(BaseModel):
    plan_id: str
    max_shift_min: int = 30
    separation: SeparationRules = Field(default_factory=SeparationRules)
    include_civil: bool = True


class DeconflictAirspaceOutput(BaseModel):
    status: str
    adjusted_flights: list[FlightAdjustment]
    remaining_conflicts: list[Conflict]
    total_shift_min: int
