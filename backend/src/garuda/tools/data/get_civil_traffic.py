"""Data tool `get_civil_traffic`. Backing: queries/adsb.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class TrafficItem(BaseModel):
    icao24: str
    callsign: str | None = None
    lat: float
    lon: float
    alt_m: float | None = None


class GetCivilTrafficInput(BaseModel):
    polygon: list[tuple[float, float]]
    since: datetime


class GetCivilTrafficOutput(BaseModel):
    total: int
    items: list[TrafficItem]


SPEC = ToolSpec(
    name="get_civil_traffic",
    description="ADS-B tracks in an area since a time. Use for deconfliction context; not for threat tracks.",
    input_model=GetCivilTrafficInput,
    output_model=GetCivilTrafficOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetCivilTrafficOutput(
    total=1,
    items=[TrafficItem(icao24="abc123", callsign="AIC101", lat=28.5, lon=77.1, alt_m=10000.0)],
)


async def get_civil_traffic(payload: GetCivilTrafficInput, ctx: ToolContext) -> GetCivilTrafficOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
