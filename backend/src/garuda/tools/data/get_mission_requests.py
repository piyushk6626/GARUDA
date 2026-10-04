"""Data tool `get_mission_requests`. Backing: NEW queries/missions.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetMissionRequestsInput(BaseModel):
    pass


class GetMissionRequestsOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_mission_requests",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetMissionRequestsInput,
    output_model=GetMissionRequestsOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_mission_requests(payload: GetMissionRequestsInput, ctx: ToolContext) -> GetMissionRequestsOutput:
    raise NotImplementedError
