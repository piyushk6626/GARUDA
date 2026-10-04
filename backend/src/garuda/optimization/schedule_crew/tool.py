from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.schedule_crew.schemas import ScheduleCrewInput, ScheduleCrewOutput, SlotGap
from garuda.tools.stubs import STUB_CREW, fixed

SPEC = ToolSpec(
    name="schedule_crew",
    description="Assign qualified, rested crew to the sortie slots of a plan.",
    input_model=ScheduleCrewInput,
    output_model=ScheduleCrewOutput,
    side_effects="draft",
    timeout_s=40,
)

STUB_OUTPUT = ScheduleCrewOutput(
    status="FEASIBLE",
    crew_assignments=[STUB_CREW],
    unfilled_slots=[SlotGap(slot_id="S-002", reason="REST", detail="skeleton")],
    fatigue_after={"C-001": 0.35},
    kpis={"filled_slots": 1.0, "mean_fatigue": 0.35},
)


async def schedule_crew(payload: ScheduleCrewInput, ctx: ToolContext) -> ScheduleCrewOutput:
    """Skeleton: same output for every input until the solver is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_crew)
