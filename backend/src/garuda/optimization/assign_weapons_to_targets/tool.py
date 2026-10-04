from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.assign_weapons_to_targets.schemas import (
    AssignWeaponsToTargetsInput,
    AssignWeaponsToTargetsOutput,
    WeaponAllocation,
)
from garuda.tools.stubs import fixed

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
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_weapons_to_targets)
