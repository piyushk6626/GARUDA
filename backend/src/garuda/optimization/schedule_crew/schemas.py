"""Input/output models from docs/03_optimization_layer/cpsat_crew_scheduling.md."""

from typing import Literal

from pydantic import BaseModel, Field

from garuda.contracts.plan import CrewAssignment


class SlotGap(BaseModel):
    slot_id: str
    reason: Literal["NO_QUALIFIED", "REST", "MEDICAL"]
    detail: str = ""


class ScheduleCrewInput(BaseModel):
    plan_id: str
    base_ids: list[str] | None = None
    max_duty_hours: float = 14
    min_rest_hours: float = 12
    time_limit_s: int = 20


class ScheduleCrewOutput(BaseModel):
    status: str
    crew_assignments: list[CrewAssignment]
    unfilled_slots: list[SlotGap]
    fatigue_after: dict[str, float]
    kpis: dict[str, float] = Field(default_factory=dict)
