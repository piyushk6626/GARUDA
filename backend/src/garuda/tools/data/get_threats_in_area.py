"""Data tool `get_threats_in_area`. Backing: NEW queries/threats.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetThreatsInAreaInput(BaseModel):
    pass


class GetThreatsInAreaOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_threats_in_area",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetThreatsInAreaInput,
    output_model=GetThreatsInAreaOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_threats_in_area(payload: GetThreatsInAreaInput, ctx: ToolContext) -> GetThreatsInAreaOutput:
    raise NotImplementedError
