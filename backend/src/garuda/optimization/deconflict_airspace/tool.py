from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.deconflict_airspace.schemas import (
    Conflict,
    DeconflictAirspaceInput,
    DeconflictAirspaceOutput,
    FlightAdjustment,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="deconflict_airspace",
    description="Shift times/altitudes so flights respect separation and zone capacity.",
    input_model=DeconflictAirspaceInput,
    output_model=DeconflictAirspaceOutput,
    side_effects="draft",
    timeout_s=30,
)

STUB_OUTPUT = DeconflictAirspaceOutput(
    status="FEASIBLE",
    adjusted_flights=[FlightAdjustment(flight="M-1001", shift_min=5, altitude_block="FL250")],
    remaining_conflicts=[Conflict(flight_a="M-1001", flight_b="M-1002", zone="CTR-B1", detail="skeleton")],
    total_shift_min=5,
)


async def deconflict_airspace(payload: DeconflictAirspaceInput, ctx: ToolContext) -> DeconflictAirspaceOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, deconflict_airspace)
