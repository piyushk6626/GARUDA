"""Input/output models from docs/03_optimization_layer/monte_carlo_plan_evaluation.md."""

from typing import Literal

from pydantic import BaseModel, Field

from garuda.contracts.results import Distribution, Sensitivity

Stress = Literal["none", "degraded_weather", "heavy_threat", "tanker_loss"]


class EvaluatePlanMonteCarloInput(BaseModel):
    plan_id: str
    n_runs: int = Field(default=5000, ge=100, le=50_000)
    seed: int = 42
    stress: Stress = "none"
    value_threshold: float | None = Field(
        default=None,
        ge=0,
        description="Value the plan should reach. Defaults to 80% of the planned value.",
    )


class EvaluatePlanMonteCarloOutput(BaseModel):
    value: Distribution
    mission_success: dict[str, float]
    expected_losses: float
    p_value_above_threshold: float
    cvar_5: float
    sensitivities: list[Sensitivity]
    weakest_links: list[str]
