"""Data tool `get_airspace`. Backing: queries/airspace.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetAirspaceInput(BaseModel):
    pass


class GetAirspaceOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_airspace",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetAirspaceInput,
    output_model=GetAirspaceOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_airspace(payload: GetAirspaceInput, ctx: ToolContext) -> GetAirspaceOutput:
    raise NotImplementedError
