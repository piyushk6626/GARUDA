"""Data tool `get_crew_status`. Backing: queries/crew.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetCrewStatusInput(BaseModel):
    pass


class GetCrewStatusOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_crew_status",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetCrewStatusInput,
    output_model=GetCrewStatusOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_crew_status(payload: GetCrewStatusInput, ctx: ToolContext) -> GetCrewStatusOutput:
    raise NotImplementedError
