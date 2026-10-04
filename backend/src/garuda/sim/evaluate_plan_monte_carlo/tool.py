import asyncio

from garuda.contracts.results import Distribution, Sensitivity
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.sim.evaluate_plan_monte_carlo.explain import explain
from garuda.sim.evaluate_plan_monte_carlo.model import solve
from garuda.sim.evaluate_plan_monte_carlo.problem import Problem, build_problem
from garuda.sim.evaluate_plan_monte_carlo.schemas import (
    EvaluatePlanMonteCarloInput,
    EvaluatePlanMonteCarloOutput,
)

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
    """Evaluate a stored plan without writes or changes to shared contracts.

    The threshold is plan.kpis.value; the local RNG uses payload.seed.
    CPU work is offloaded while the shared run_cpu remains a stub.
    STUB_OUTPUT remains for compatibility with the phase-2 graph skeleton.
    """
    problem = build_problem(payload, ctx)
    return await asyncio.to_thread(_evaluate, problem)


def _evaluate(problem: Problem) -> EvaluatePlanMonteCarloOutput:
    return explain(problem, solve(problem))


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, evaluate_plan_monte_carlo)
