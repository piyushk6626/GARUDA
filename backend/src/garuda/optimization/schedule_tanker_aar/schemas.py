"""Input/output models from docs/03_optimization_layer/vrp_tanker_aar_scheduling.md."""

from datetime import datetime

from pydantic import BaseModel, Field


class AarStop(BaseModel):
    aarp: str
    receiver: str
    time: datetime
    offload_kg: float


class TankerRoute(BaseModel):
    tail: str
    sequence: list[AarStop]


class RefuelEvent(BaseModel):
    mission_id: str
    tail_no: str
    fuel_demand_kg: float
    reason: str = ""


class ScheduleTankerAarInput(BaseModel):
    plan_id: str
    tanker_tails: list[str] | None = None
    candidate_aarp_ids: list[str] | None = None
    time_limit_s: int = 15


class ScheduleTankerAarOutput(BaseModel):
    tanker_routes: list[TankerRoute]
    unserved: list[RefuelEvent]
    spare_fuel_kg: dict[str, float]
    kpis: dict[str, float] = Field(default_factory=dict)
