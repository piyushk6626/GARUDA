from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.assign_weapons_to_targets.schemas import AssignWeaponsToTargetsInput, AssignWeaponsToTargetsOutput

SPEC = ToolSpec(
    name="assign_weapons_to_targets",
    description="Allocate weapons to targets maximising expected destroyed value.",
    input_model=AssignWeaponsToTargetsInput,
    output_model=AssignWeaponsToTargetsOutput,
    side_effects="draft",
    timeout_s=30,
)


async def assign_weapons_to_targets(payload: AssignWeaponsToTargetsInput, ctx: ToolContext) -> AssignWeaponsToTargetsOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_weapons_to_targets)
