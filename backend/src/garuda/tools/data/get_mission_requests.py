"""Data tool `get_mission_requests`. Backing: queries/missions.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.geo import TimeWindow
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.queries.missions import get_mission_requests as query_mission_requests


class MissionItem(BaseModel):
    mission_id: str
    type: str
    status: str
    priority: int


class GetMissionRequestsInput(BaseModel):
    status: list[str] | None = None
    types: list[str] | None = None
    priority_max: int | None = None
    window: TimeWindow | None = None


class GetMissionRequestsOutput(BaseModel):
    total: int
    items: list[MissionItem]


SPEC = ToolSpec(
    name="get_mission_requests",
    description="Pending and planned missions. Use to start a plan; not to assign aircraft.",
    input_model=GetMissionRequestsInput,
    output_model=GetMissionRequestsOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetMissionRequestsOutput(
    total=1,
    items=[MissionItem(mission_id="M-1001", type="STRIKE", status="REQUESTED", priority=2)],
)


async def get_mission_requests(payload: GetMissionRequestsInput, ctx: ToolContext) -> GetMissionRequestsOutput:
    """Filter snapshot missions and return only the public item fields."""
    missions = query_mission_requests(
        ctx.scenario.snapshot().missions,
        status=payload.status,
        types=payload.types,
        priority_max=payload.priority_max,
        window=payload.window,
    )
    items = [
        MissionItem(
            mission_id=mission.mission_id,
            type=mission.type,
            status=mission.status,
            priority=mission.priority,
        )
        for mission in missions
    ]
    return GetMissionRequestsOutput(total=len(items), items=items)
