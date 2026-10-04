"""Data tool `get_airspace`. Backing: queries/airspace.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class AirspaceItem(BaseModel):
    zone_id: str
    name: str
    type: str
    floor_ft: int
    ceiling_ft: int


class GetAirspaceInput(BaseModel):
    route: list[tuple[float, float]] | None = None
    polygon: list[tuple[float, float]] | None = None
    altitude_ft: int | None = None
    time: datetime | None = None


class GetAirspaceOutput(BaseModel):
    total: int
    items: list[AirspaceItem]


SPEC = ToolSpec(
    name="get_airspace",
    description="Zones active along a route, time, and altitude. Use before routing; not for deconfliction.",
    input_model=GetAirspaceInput,
    output_model=GetAirspaceOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetAirspaceOutput(
    total=1,
    items=[AirspaceItem(zone_id="CTR-B1", name="Base 1 CTR", type="CTR", floor_ft=0, ceiling_ft=10000)],
)


async def get_airspace(payload: GetAirspaceInput, ctx: ToolContext) -> GetAirspaceOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
