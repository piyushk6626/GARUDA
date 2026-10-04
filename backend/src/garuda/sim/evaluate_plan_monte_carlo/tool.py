from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloInput, EvaluatePlanMonteCarloOutput

SPEC = ToolSpec(
    name="evaluate_plan_monte_carlo",
    description="Simulate a plan N times for outcome distributions and robustness.",
    input_model=EvaluatePlanMonteCarloInput,
    output_model=EvaluatePlanMonteCarloOutput,
    side_effects="none",
    timeout_s=30,
)


async def evaluate_plan_monte_carlo(payload: EvaluatePlanMonteCarloInput, ctx: ToolContext) -> EvaluatePlanMonteCarloOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, evaluate_plan_monte_carlo)
