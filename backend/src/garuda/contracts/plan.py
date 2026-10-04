"""The single plan shape every downstream tool reads and writes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from garuda.contracts.geo import Waypoint

ReasonCode = Literal[
    "NO_QUALIFIED_CREW",
    "NO_WEAPON_STOCK",
    "WEATHER",
    "THREAT_RISK",
    "NO_AIRCRAFT",
    "NO_TANKER",
    "BASE_CAPACITY",
    "WINDOW",
]
PlanStatus = Literal["DRAFT", "AWAITING_APPROVAL", "APPROVED", "REJECTED", "COMMITTED"]


class Assignment(BaseModel):
    tail_no: str
    config_id: str
    mission_id: str
    depart: datetime
    tot: datetime


class UnassignedMission(BaseModel):
    mission_id: str
    reason: ReasonCode
    detail: str = ""


class SortieSlot(BaseModel):
    slot_id: str
    mission_id: str
    tail_no: str
    role: str
    aircraft_type: str
    start: datetime
    end: datetime
    night: bool = False
    needs_aar: bool = False


class CrewAssignment(BaseModel):
    crew_id: str
    slot_id: str
    role: str


class FlightLeg(BaseModel):
    """A flown route for one assignment; consumed by airspace, tanker, Monte Carlo."""

    mission_id: str
    tail_no: str
    waypoints: list[Waypoint]
    zones: list[str] = Field(default_factory=list)
    needs_aar: bool = False
    fuel_demand_kg: float = 0.0


class PlanKPIs(BaseModel):
    value: float = 0.0
    fulfilment_pct: float = 0.0
    sorties: int = 0
    risk: float = 0.0


class PlanDraft(BaseModel):
    plan_id: str
    parent_plan_id: str | None = None
    scenario_time: datetime
    status: PlanStatus = "DRAFT"
    assignments: list[Assignment] = Field(default_factory=list)
    unassigned: list[UnassignedMission] = Field(default_factory=list)
    sortie_slots: list[SortieSlot] = Field(default_factory=list)
    crew_assignments: list[CrewAssignment] = Field(default_factory=list)
    legs: list[FlightLeg] = Field(default_factory=list)
    kpis: PlanKPIs = Field(default_factory=PlanKPIs)


class PlanDiff(BaseModel):
    added: list[Assignment] = Field(default_factory=list)
    removed: list[Assignment] = Field(default_factory=list)
    changed: list[tuple[Assignment, Assignment]] = Field(default_factory=list)

