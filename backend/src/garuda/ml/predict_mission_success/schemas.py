"""Input/output models from docs/03_optimization_layer/mission_success_probability.md."""

from pydantic import BaseModel, Field


class PackageSpec(BaseModel):
    mission_id: str
    aircraft_type: str
    config_id: str
    support: list[str] = Field(default_factory=list)


class MissionSuccess(BaseModel):
    mission_id: str
    p_success: float
    p_return: float
    ci_low: float
    ci_high: float
    drivers: list[str]


class PredictMissionSuccessInput(BaseModel):
    plan_id: str | None = None
    mission_id: str | None = None
    package: PackageSpec | None = None


class PredictMissionSuccessOutput(BaseModel):
    items: list[MissionSuccess]
    plan_expected_value: float
