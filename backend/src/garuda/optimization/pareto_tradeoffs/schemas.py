"""Input/output models from docs/03_optimization_layer/multi_objective_tradeoffs.md."""

from typing import Literal

from pydantic import BaseModel, Field

ObjectiveName = Literal["value", "risk", "munitions", "fatigue", "disruption"]


class PlanOption(BaseModel):
    plan_id: str
    label: str
    objectives: dict[str, float]
    key_differences: list[str]


class ParetoTradeoffsInput(BaseModel):
    base_plan_id: str
    objectives: list[ObjectiveName] = Field(default_factory=lambda: ["value", "risk"])
    n_points: int = 5
    method: Literal["epsilon", "weighted", "nsga2"] = "epsilon"
    time_limit_s_per_solve: int = 15


class ParetoTradeoffsOutput(BaseModel):
    options: list[PlanOption]
    ideal: dict[str, float]
    nadir: dict[str, float]
    recommendation: str | None = None
