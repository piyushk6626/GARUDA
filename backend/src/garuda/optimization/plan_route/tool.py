from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.plan_route.schemas import PlanRouteInput, PlanRouteOutput, RouteSummary
from garuda.tools.stubs import STUB_WAYPOINT, fixed

SPEC = ToolSpec(
    name="plan_route",
    description="Threat-aware route between two points; not for airspace slot deconfliction.",
    input_model=PlanRouteInput,
    output_model=PlanRouteOutput,
    side_effects="none",
    timeout_s=20,
)

STUB_OUTPUT = PlanRouteOutput(
    waypoints=[STUB_WAYPOINT],
    distance_km=420.0,
    fuel_kg=1800.0,
    cumulative_risk=0.12,
    airspace_crossed=["CTR-B1"],
    alternatives=[RouteSummary(label="safest", distance_km=460.0, cumulative_risk=0.08)],
)


async def plan_route(payload: PlanRouteInput, ctx: ToolContext) -> PlanRouteOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, plan_route)
