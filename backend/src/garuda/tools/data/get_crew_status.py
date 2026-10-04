"""Data tool `get_crew_status`. Backing: queries/crew.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class CrewStatusItem(BaseModel):
    crew_id: str
    role: str
    base_id: str
    qualification: str
    fatigue_score: float
    available: bool


class GetCrewStatusInput(BaseModel):
    base_ids: list[str] | None = None
    role: str | None = None
    qualification: str | None = None
    max_fatigue: float | None = None
    available_from: datetime | None = None
    available_to: datetime | None = None


class GetCrewStatusOutput(BaseModel):
    total: int
    items: list[CrewStatusItem]


SPEC = ToolSpec(
    name="get_crew_status",
    description="Crew by qualification, fatigue, and availability. Use before scheduling; not for a fatigue forecast.",
    input_model=GetCrewStatusInput,
    output_model=GetCrewStatusOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetCrewStatusOutput(
    total=1,
    items=[
        CrewStatusItem(
            crew_id="C-001",
            role="PILOT",
            base_id="B1",
            qualification="Su-30MKI",
            fatigue_score=0.3,
            available=True,
        )
    ],
)


async def get_crew_status(payload: GetCrewStatusInput, ctx: ToolContext) -> GetCrewStatusOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
