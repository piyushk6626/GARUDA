from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.assign_missions.schemas import AssignMissionsInput, AssignMissionsOutput
from garuda.tools.stubs import STUB_ASSIGNMENT, STUB_KPIS, STUB_UNASSIGNED, fixed

SPEC = ToolSpec(
    name="assign_missions",
    description="Assign aircraft and load configs to missions (MILP). Use for new plans; not for repairing an existing plan after an event.",
    input_model=AssignMissionsInput,
    output_model=AssignMissionsOutput,
    side_effects="draft",
    timeout_s=60,
)

STUB_OUTPUT = AssignMissionsOutput(
    plan_id="plan-stub",
    status="FEASIBLE",
    assignments=[STUB_ASSIGNMENT],
    unassigned=[STUB_UNASSIGNED],
    kpis=STUB_KPIS,
    binding_constraints=["weapon_stock:B2"],
)


async def assign_missions(payload: AssignMissionsInput, ctx: ToolContext) -> AssignMissionsOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_missions)
