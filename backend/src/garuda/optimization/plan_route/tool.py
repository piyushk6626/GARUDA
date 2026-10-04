import asyncio
import warnings

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.plan_route.explain import explain
from garuda.optimization.plan_route.model import solve
from garuda.optimization.plan_route.problem import build_problem
from garuda.optimization.plan_route.schemas import PlanRouteInput, PlanRouteOutput, RouteSummary
from garuda.tools.stubs import STUB_WAYPOINT

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
    """Plan one leg without writing a draft or changing the public contract."""
    # The shared CPU executor is still a stub. Keep work off the event loop.
    return await asyncio.to_thread(_compute, payload, ctx)


def _compute(payload, ctx):
    problem = build_problem(payload, ctx)
    solution = solve(problem)
    if solution.risk_cap_exceeded:
        warnings.warn(
            "No route satisfying max_risk was found; returning the least-risk route found above the cap. "
            "Consider SEAD or changing the altitude profile.",
            RuntimeWarning,
            stacklevel=2,
        )
    return explain(problem, solution)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, plan_route)
