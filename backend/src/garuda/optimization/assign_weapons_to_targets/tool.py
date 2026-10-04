import asyncio

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.optimization.assign_weapons_to_targets.baseline import baseline
from garuda.optimization.assign_weapons_to_targets.explain import explain
from garuda.optimization.assign_weapons_to_targets.model import SolveOptions, solve
from garuda.optimization.assign_weapons_to_targets.problem import build_problem
from garuda.optimization.assign_weapons_to_targets.schemas import (
    AssignWeaponsToTargetsInput,
    AssignWeaponsToTargetsOutput,
    WeaponAllocation,
)

SPEC = ToolSpec(
    name="assign_weapons_to_targets",
    description="Allocate weapons to targets maximising expected destroyed value.",
    input_model=AssignWeaponsToTargetsInput,
    output_model=AssignWeaponsToTargetsOutput,
    side_effects="draft",
    timeout_s=30,
)

STUB_OUTPUT = AssignWeaponsToTargetsOutput(
    allocations=[WeaponAllocation(target_id="T-001", weapon_code="AGM-1", count=2, base_id="B1")],
    expected_destroyed_value=1.0,
    kill_prob_by_target={"T-001": 0.8},
    stock_remaining={"AGM-1": 10},
)


async def assign_weapons_to_targets(
    payload: AssignWeaponsToTargetsInput, ctx: ToolContext
) -> AssignWeaponsToTargetsOutput:
    """Assign weapons from base stock onto the requested targets.

    ``method="greedy"`` is the default. ``method="milp"`` solves the
    piecewise-linear model and needs PuLP.
    """
    problem = build_problem(payload, ctx)
    options = SolveOptions(time_limit_s=SPEC.timeout_s, seed=ctx.seed)
    try:
        if payload.method == "milp":
            solution = await asyncio.to_thread(solve, problem, options)
        else:
            solution = await asyncio.to_thread(baseline, problem)
    except RuntimeError as exc:
        raise ToolInputError(str(exc)) from exc
    return explain(problem, solution)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_weapons_to_targets)
