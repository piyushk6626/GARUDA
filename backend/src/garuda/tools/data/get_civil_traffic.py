"""Data tool `get_civil_traffic`. Backing: queries/adsb.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetCivilTrafficInput(BaseModel):
    pass


class GetCivilTrafficOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_civil_traffic",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetCivilTrafficInput,
    output_model=GetCivilTrafficOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_civil_traffic(payload: GetCivilTrafficInput, ctx: ToolContext) -> GetCivilTrafficOutput:
    raise NotImplementedError
