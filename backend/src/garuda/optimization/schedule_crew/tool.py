import asyncio

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.optimization.schedule_crew.explain import explain
from garuda.optimization.schedule_crew.model import solve
from garuda.optimization.schedule_crew.problem import build_problem
from garuda.optimization.schedule_crew.schemas import ScheduleCrewInput, ScheduleCrewOutput, SlotGap
from garuda.tools.executor import run_cpu
from garuda.tools.stubs import STUB_CREW

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
    """Schedule the existing draft's seats and persist only its crew assignments."""
    problem = build_problem(payload, ctx)
    try:
        solution = await run_cpu(solve, problem)
    except NotImplementedError:
        # The shared process executor is still a skeleton. Keep this tool usable
        # in isolation and leave that shared implementation to its owner.
        solution = await asyncio.to_thread(solve, problem)
    output = explain(problem, solution)
    if solution.has_solution:
        if ctx.plans.get(payload.plan_id) != problem.plan:
            raise ToolInputError("plan changed during crew scheduling; retry with the current draft")
        draft = problem.plan.model_copy(deep=True, update={
            "crew_assignments": [assignment.model_copy(deep=True) for assignment in output.crew_assignments],
        })
        ctx.plans.put(draft)
    return output


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, schedule_crew)
