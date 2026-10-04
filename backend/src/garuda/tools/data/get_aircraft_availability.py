"""Data tool `get_aircraft_availability`. Backing: queries/aircraft.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from collections import Counter
from datetime import datetime
import math

from pydantic import BaseModel, Field

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.schemas.aircraft import AircraftStatus, canonical_type


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

# Compatibility fixture for the separate data graph scaffold.
# The callable tool below reads the scenario instead.
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
    """Read a snapshot, applying OR within filters and AND across filters.

    All requested capabilities must be present. Explicit empty base/type/status
    lists match nothing. Totals and status counts cover every match before the
    tail-number-sorted items are limited. Past/current requests use the current
    snapshot; this tool does not reconstruct historical states.

    Future requests classify provider P(FMC) >= 0.5 as FMC. Below that threshold,
    retain an existing non-FMC status, or classify currently FMC aircraft as NMC.
    This binary forecast cannot predict which non-FMC condition will occur.
    Other state fields remain snapshot values, not forecasts.
    """
    if not 1 <= payload.limit <= 200:
        raise ToolInputError("limit must be between 1 and 200")
    if payload.available_at is not None:
        _require_aware(payload.available_at, "available_at")
        _require_aware(ctx.scenario_time, "scenario_time")

    base_ids = None if payload.base_ids is None else set(payload.base_ids)
    if base_ids is not None:
        known_bases = set(ctx.scenario.base_ids())
        unknown = base_ids - known_bases
        if unknown:
            raise ToolInputError(
                f"unknown base_id {', '.join(sorted(unknown))}; "
                f"valid: {', '.join(sorted(known_bases))}"
            )
    try:
        types = (
            None if payload.types is None
            else {canonical_type(name) for name in payload.types}
        )
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    statuses = None if payload.statuses is None else set(payload.statuses)
    capabilities = set(payload.capabilities or [])
    at = payload.available_at
    future = at is not None and at > ctx.scenario_time
    matched: list[AircraftState] = []
    for airframe in ctx.scenario.snapshot().aircraft:
        if (
            (base_ids is not None and airframe.base_id not in base_ids)
            or (types is not None and airframe.type not in types)
            or not capabilities.issubset(airframe.capabilities)
        ):
            continue
        status = airframe.status
        if future and at is not None:
            status = _forecast_status(airframe.tail_no, status, at, ctx)
        if statuses is not None and status not in statuses:
            continue
        matched.append(
            AircraftState(
                tail_no=airframe.tail_no,
                type=airframe.type,
                base_id=airframe.base_id,
                status=status,
                fuel_kg=airframe.fuel_kg,
                hours_to_next_inspection=airframe.hours_to_next_inspection,
                capabilities=list(airframe.capabilities),
            )
        )
    matched.sort(key=lambda airframe: airframe.tail_no)
    return GetAircraftAvailabilityOutput(
        total=len(matched),
        items=matched[:payload.limit],
        counts_by_status=dict(sorted(Counter(item.status for item in matched).items())),
    )


def _require_aware(moment: datetime, field: str) -> None:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ToolInputError(f"{field} must be timezone-aware")


def _forecast_status(
    tail_no: str, current_status: AircraftStatus, at: datetime, ctx: ToolContext
) -> AircraftStatus:
    try:
        provider = ctx.providers.availability
        probability = provider.p_fmc(tail_no, at)
    except (AttributeError, NotImplementedError) as exc:
        raise RuntimeError(
            "availability forecast provider is not configured for future available_at"
        ) from exc
    try:
        valid = (
            not isinstance(probability, bool)
            and math.isfinite(probability)
            and 0 <= probability <= 1
        )
    except TypeError:
        valid = False
    if not valid:
        raise RuntimeError(
            f"availability provider returned an invalid FMC probability for {tail_no}; "
            "expected a finite number in [0, 1]"
        )
    if probability >= 0.5:
        return "FMC"
    return "NMC" if current_status == "FMC" else current_status
