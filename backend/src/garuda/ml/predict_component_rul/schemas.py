"""Input/output models from docs/03_optimization_layer/component_failure_survival.md."""

from pydantic import BaseModel


class ComponentRisk(BaseModel):
    tail: str
    component: str
    hours_since_overhaul: float
    p_fail_next: float
    rul_p10: float
    rul_p50: float
    rul_p90: float


class MaintenanceAdvice(BaseModel):
    tail: str
    component: str
    action: str


class PredictComponentRulInput(BaseModel):
    tail_nos: list[str] | None = None
    sortie_hours_ahead: float = 6.0
    component_types: list[str] | None = None


class PredictComponentRulOutput(BaseModel):
    items: list[ComponentRisk]
    aircraft_risk: dict[str, float]
    recommended_maintenance: list[MaintenanceAdvice]
