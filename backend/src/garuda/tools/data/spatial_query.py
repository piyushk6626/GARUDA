"""Data tool `spatial_query`. Backing: queries/gis.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class SpatialQueryInput(BaseModel):
    pass


class SpatialQueryOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="spatial_query",
    description="TODO(phase1): state when to use and not use.",
    input_model=SpatialQueryInput,
    output_model=SpatialQueryOutput,
    side_effects="none",
    timeout_s=10,
)


async def spatial_query(payload: SpatialQueryInput, ctx: ToolContext) -> SpatialQueryOutput:
    raise NotImplementedError
