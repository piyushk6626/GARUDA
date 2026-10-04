"""Data tool `get_aircraft_availability`. Backing: queries/aircraft.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from datetime import datetime

from pydantic import BaseModel, Field

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.schemas.aircraft import AircraftStatus
from garuda.tools.stubs import fixed


class AircraftState(BaseModel):
    tail_no: str
    type: str
    base_id: str
    status: AircraftStatus
    fuel_kg: float
    hours_to_next_inspection: float
    capabilities: list[str]


class GetAircraftAvailabilityInput(BaseModel):
    base_ids: list[str] | None = None
    types: list[str] | None = None
    statuses: list[AircraftStatus] | None = None
    capabilities: list[str] | None = None
    available_at: datetime | None = None
    limit: int = Field(50, le=200)


class GetAircraftAvailabilityOutput(BaseModel):
    total: int
    items: list[AircraftState]
    counts_by_status: dict[str, int]


SPEC = ToolSpec(
    name="get_aircraft_availability",
    description="Filter aircraft by base, type, status, or time. Use before assignment; not for forecasting hours ahead.",
    input_model=GetAircraftAvailabilityInput,
    output_model=GetAircraftAvailabilityOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = GetAircraftAvailabilityOutput(
    total=1,
    items=[
        AircraftState(
            tail_no="SU-A0123",
            type="Su-30MKI",
            base_id="B1",
            status="FMC",
            fuel_kg=5200.0,
            hours_to_next_inspection=37.5,
            capabilities=["BVR", "AG_PGM"],
        )
    ],
    counts_by_status={"FMC": 1},
)


async def get_aircraft_availability(
    payload: GetAircraftAvailabilityInput, ctx: ToolContext
) -> GetAircraftAvailabilityOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
