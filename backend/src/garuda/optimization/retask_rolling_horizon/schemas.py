"""Input/output models from docs/03_optimization_layer/rolling_horizon_dynamic_retasking.md."""

from typing import Literal

from pydantic import BaseModel

from garuda.contracts.events import Event
from garuda.contracts.plan import PlanDiff


class RetaskRollingHorizonInput(BaseModel):
    plan_id: str
    event: Event
    freeze_hours: float = 2.0
    stability_weight: float = 5.0
    mode: Literal["fast", "full"] = "fast"
    time_limit_s: int = 15


class RetaskRollingHorizonOutput(BaseModel):
    new_plan_id: str
    diff: PlanDiff
    missions_degraded: list[str]
    kpi_delta: dict[str, float]
    status: str
