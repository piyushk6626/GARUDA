"""Input/output models from docs/03_optimization_layer/milp_mission_aircraft_assignment.md."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from garuda.contracts.plan import Assignment, PlanKPIs, UnassignedMission


class ObjectiveWeights(BaseModel):
    risk: float = 0.2
    effect: float = 0.3


class AssignMissionsInput(BaseModel):
    scenario_time: datetime
    horizon_hours: int = 72
    mission_ids: list[str] | None = None
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    locked_assignments: list[Assignment] = Field(default_factory=list)
    time_limit_s: int = 30


class AssignMissionsOutput(BaseModel):
    plan_id: str
    status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE"]
    assignments: list[Assignment]
    unassigned: list[UnassignedMission]
    kpis: PlanKPIs
    binding_constraints: list[str]
