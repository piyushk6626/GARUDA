"""Input/output models from docs/03_optimization_layer/crew_fatigue_model.md."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CrewFatigue(BaseModel):
    crew_id: str
    now: float
    projected_peak: float
    risk_band: Literal["GREEN", "AMBER", "RED"]
    drivers: list[str]


class AssessCrewFatigueInput(BaseModel):
    crew_ids: list[str] | None = None
    base_ids: list[str] | None = None
    plan_id: str | None = None
    at_time: datetime | None = None


class AssessCrewFatigueOutput(BaseModel):
    items: list[CrewFatigue]
    base_summary: dict[str, dict[str, int]]
