"""Input/output models from docs/03_optimization_layer/monte_carlo_plan_evaluation.md."""

from typing import Literal

from pydantic import BaseModel

from garuda.contracts.results import Distribution, Sensitivity


class EvaluatePlanMonteCarloInput(BaseModel):
    plan_id: str
    n_runs: int = 5000
    seed: int = 42
    stress: Literal["none", "degraded_weather", "heavy_threat", "tanker_loss"] = "none"


class EvaluatePlanMonteCarloOutput(BaseModel):
    value: Distribution
    mission_success: dict[str, float]
    expected_losses: float
    p_value_above_threshold: float
    cvar_5: float
    sensitivities: list[Sensitivity]
    weakest_links: list[str]
