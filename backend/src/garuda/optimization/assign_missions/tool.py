from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.assign_missions.schemas import AssignMissionsInput, AssignMissionsOutput

SPEC = ToolSpec(
    name="assign_missions",
    description="Assign aircraft and load configs to missions (MILP). Use for new plans; not for repairing an existing plan after an event.",
    input_model=AssignMissionsInput,
    output_model=AssignMissionsOutput,
    side_effects="draft",
    timeout_s=60,
)


async def assign_missions(payload: AssignMissionsInput, ctx: ToolContext) -> AssignMissionsOutput:
    """TODO(phase1): build_problem -> run_cpu(solve) -> explain -> output."""
    raise NotImplementedError


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_missions)
