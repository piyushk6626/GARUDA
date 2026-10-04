"""Fixed clock and sample entities shared by skeleton tool outputs.

Every skeleton returns these same values until a real solver is wired.
"""

from datetime import datetime, timezone
from typing import TypeVar

from pydantic import BaseModel

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import Assignment, CrewAssignment, PlanKPIs, UnassignedMission

ModelT = TypeVar("ModelT", bound=BaseModel)

STUB_TIME = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
STUB_TIME_END = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)

STUB_ASSIGNMENT = Assignment(
    tail_no="SU-A0123",
    config_id="CFG-STRIKE-1",
    mission_id="M-1001",
    depart=STUB_TIME,
    tot=STUB_TIME_END,
)
STUB_UNASSIGNED = UnassignedMission(mission_id="M-1002", reason="NO_AIRCRAFT", detail="skeleton")
STUB_KPIS = PlanKPIs(value=1.0, fulfilment_pct=0.5, sorties=1, risk=0.2)
STUB_CREW = CrewAssignment(crew_id="C-001", slot_id="S-001", role="PILOT")
STUB_WAYPOINT = Waypoint(lat=28.61, lon=77.21, alt_ft=25000, eta=STUB_TIME)


def fixed(output: ModelT) -> ModelT:
    """Return a deep copy so callers cannot mutate the shared stub."""
    return output.model_copy(deep=True)
