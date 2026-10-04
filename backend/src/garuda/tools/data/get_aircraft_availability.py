"""Data tool `get_aircraft_availability`. Backing: queries/aircraft.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetAircraftAvailabilityInput(BaseModel):
    pass


class GetAircraftAvailabilityOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_aircraft_availability",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetAircraftAvailabilityInput,
    output_model=GetAircraftAvailabilityOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_aircraft_availability(payload: GetAircraftAvailabilityInput, ctx: ToolContext) -> GetAircraftAvailabilityOutput:
    raise NotImplementedError
