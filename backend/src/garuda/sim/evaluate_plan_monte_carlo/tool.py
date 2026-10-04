from garuda.contracts.results import Distribution, Sensitivity
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.evaluate_plan_monte_carlo.schemas import (
    EvaluatePlanMonteCarloInput,
    EvaluatePlanMonteCarloOutput,
)
from garuda.tools.stubs import fixed

SPEC = ToolSpec(
    name="evaluate_plan_monte_carlo",
    description="Simulate a plan N times for outcome distributions and robustness.",
    input_model=EvaluatePlanMonteCarloInput,
    output_model=EvaluatePlanMonteCarloOutput,
    side_effects="none",
    timeout_s=30,
)

STUB_OUTPUT = EvaluatePlanMonteCarloOutput(
    value=Distribution(mean=1.0, p5=0.4, p50=1.0, p95=1.4),
    mission_success={"M-1001": 0.72},
    expected_losses=0.1,
    p_value_above_threshold=0.8,
    cvar_5=0.35,
    sensitivities=[Sensitivity(factor="weather", effect=-0.15)],
    weakest_links=["M-1002"],
)


async def evaluate_plan_monte_carlo(
    payload: EvaluatePlanMonteCarloInput, ctx: ToolContext
) -> EvaluatePlanMonteCarloOutput:
    """Skeleton: same output for every input until the simulator is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, evaluate_plan_monte_carlo)
