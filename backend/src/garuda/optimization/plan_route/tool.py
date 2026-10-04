from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.plan_route.schemas import PlanRouteInput, PlanRouteOutput

SPEC = ToolSpec(
    name="plan_route",
    description="Threat-aware route between two points; not for airspace slot deconfliction.",
    input_model=PlanRouteInput,
    output_model=PlanRouteOutput,
    side_effects="none",
    timeout_s=20,
)


async def plan_route(payload: PlanRouteInput, ctx: ToolContext) -> PlanRouteOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, plan_route)
