import asyncio
from uuid import uuid4

from garuda.contracts.plan import PlanDraft, SortieSlot
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.assign_missions.explain import explain
from garuda.optimization.assign_missions.model import solve
from garuda.optimization.assign_missions.preprocess import build_problem
from garuda.optimization.assign_missions.schemas import (
    AssignMissionsInput,
    AssignMissionsOutput,
)
from garuda.optimization.assign_missions.validate import validate

SPEC = ToolSpec(
    name="assign_missions",
    description="Assign aircraft and load configs to missions (MILP). Use for new plans; not for repairing an existing plan after an event.",
    input_model=AssignMissionsInput,
    output_model=AssignMissionsOutput,
    side_effects="draft",
    timeout_s=60,
)


def _compute(payload, ctx):
    problem = build_problem(payload, ctx)
    solution = solve(problem)
    if solution.status != "INFEASIBLE":
        errors = validate(problem, solution.selected, solution.crew)
        if errors:
            raise RuntimeError(
                "Assignment plan failed independent validation: " + "; ".join(errors)
            )
    kpis, unassigned, binding = explain(problem, solution)
    assignments = sorted(
        (problem.candidates[k].assignment for k in solution.selected),
        key=lambda a: (a.depart, a.mission_id, a.tail_no, a.config_id),
    )
    slots = []
    for key in solution.selected:
        c = problem.candidates[key]
        for role, (_, seats) in c.crew.items():
            for seat in range(seats):
                slots.append(
                    SortieSlot(
                        slot_id=f"{c.assignment.mission_id}:{c.assignment.tail_no}:{role}:{seat}",
                        mission_id=c.assignment.mission_id,
                        tail_no=c.assignment.tail_no,
                        role=role,
                        aircraft_type=c.aircraft_type,
                        start=c.assignment.depart,
                        end=c.recover,
                        night=c.night,
                    )
                )
    return solution.status, assignments, kpis, unassigned, binding, slots


async def assign_missions(
    payload: AssignMissionsInput, ctx: ToolContext
) -> AssignMissionsOutput:
    """Create a validated draft; an infeasible solve never stores a partial plan."""
    # Shared tools.executor.run_cpu is still unimplemented. Keep preparation,
    # solve and validation off the event loop until that executor is available.
    status, assignments, kpis, unassigned, binding, slots = await asyncio.to_thread(
        _compute, payload, ctx
    )
    plan_id = ""
    if status != "INFEASIBLE":
        draft = PlanDraft(
            plan_id=f"plan-{uuid4().hex}",
            scenario_time=payload.scenario_time,
            assignments=assignments,
            unassigned=unassigned,
            kpis=kpis,
            sortie_slots=slots,
        )
        plan_id = ctx.plans.put(draft)
    return AssignMissionsOutput(
        plan_id=plan_id,
        status=status,
        assignments=assignments,
        unassigned=unassigned,
        kpis=kpis,
        binding_constraints=binding,
    )


# TODO(phase1): from garuda.tools.registry import register; register(SPEC, assign_missions)
