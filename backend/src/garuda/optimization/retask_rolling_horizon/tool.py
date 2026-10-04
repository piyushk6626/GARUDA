import asyncio
from uuid import uuid4

from garuda.contracts.plan import PlanDiff, PlanDraft, UnassignedMission
from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.optimization.retask_rolling_horizon.explain import kpis, plan_diff
from garuda.optimization.retask_rolling_horizon.model import solve
from garuda.optimization.retask_rolling_horizon.problem import (
    assignment_key,
    build_problem,
)
from garuda.optimization.retask_rolling_horizon.schemas import (
    RetaskRollingHorizonInput,
    RetaskRollingHorizonOutput,
)

SPEC = ToolSpec(
    name="retask_rolling_horizon",
    description="Repair a plan after an event with minimal disruption.",
    input_model=RetaskRollingHorizonInput,
    output_model=RetaskRollingHorizonOutput,
    side_effects="draft",
    timeout_s=40,
)


async def retask_rolling_horizon(
    payload: RetaskRollingHorizonInput, ctx: ToolContext
) -> RetaskRollingHorizonOutput:
    """Repair and persist a child draft; never mutate or commit its parent."""
    problem = await asyncio.to_thread(build_problem, payload, ctx)
    # tools.executor.run_cpu is still a stub. Keep the solve off the event loop
    # locally until that shared infrastructure is implemented.
    solution = await asyncio.to_thread(solve, problem)
    return await asyncio.to_thread(finish, payload, ctx, problem, solution)


def finish(payload, ctx, problem, solution) -> RetaskRollingHorizonOutput:
    """Persist only a usable solver result; also shared by the optional graph."""
    if solution.status in {"INFEASIBLE", "ERROR"}:
        degraded = solution.degraded or problem.affected
        return RetaskRollingHorizonOutput(
            new_plan_id=payload.plan_id,
            diff=PlanDiff(),
            missions_degraded=sorted(
                degraded, key=lambda mid: (problem.missions[mid].priority, mid)
            ),
            kpi_delta={name: 0.0 for name in problem.plan.kpis.model_dump()},
            status=solution.status,
        )
    diff = plan_diff(problem.plan.assignments, solution.assignments)
    metrics = kpis(problem, solution)
    unchanged = {assignment_key(a) for a in problem.plan.assignments} & {
        assignment_key(a) for a in solution.assignments
    }
    unchanged_pairs = {(key[2], key[0]) for key in unchanged}
    slots = [
        s
        for s in problem.plan.sortie_slots
        if (s.mission_id, s.tail_no) in unchanged_pairs
    ]
    slot_ids = {s.slot_id for s in slots}
    fulfilled = {a.mission_id for a in solution.assignments}
    considered = (
        {c.assignment.mission_id for c in problem.candidates}
        | set(problem.reasons)
        | problem.affected
    )
    unassigned = []
    for mid in sorted(
        considered - fulfilled, key=lambda mid: (problem.missions[mid].priority, mid)
    ):
        reason = problem.reasons.get(mid, "NO_AIRCRAFT")
        available = [
            c for c in problem.candidates if c.valid and c.assignment.mission_id == mid
        ]
        if available and all(c.needs_aar for c in available):
            reason = "NO_TANKER"
        elif available and all(
            problem.capacities[
                c.base_id,
                c.assignment.depart.replace(minute=0, second=0, microsecond=0),
            ]
            == 0
            for c in available
        ):
            reason = "BASE_CAPACITY"
        elif available and all(
            any(
                count > problem.stocks.get((c.base_id, code), 0)
                for code, count in c.weapons.items()
            )
            for c in available
        ):
            reason = "NO_WEAPON_STOCK"
        unassigned.append(
            UnassignedMission(
                mission_id=mid,
                reason=reason,
                detail="No feasible package after event repair; resource competition or dependency may require preemption.",
            )
        )
    draft = PlanDraft(
        plan_id=f"plan-{uuid4().hex}",
        parent_plan_id=payload.plan_id,
        scenario_time=payload.event.time,
        status="DRAFT",
        assignments=solution.assignments,
        unassigned=unassigned,
        kpis=metrics,
        sortie_slots=slots,
        crew_assignments=[
            c for c in problem.plan.crew_assignments if c.slot_id in slot_ids
        ],
        legs=[
            leg
            for leg in problem.plan.legs
            if (leg.mission_id, leg.tail_no) in unchanged_pairs
            and leg.mission_id not in problem.route_replan
        ],
    )
    new_plan_id = ctx.plans.put(draft)
    return RetaskRollingHorizonOutput(
        new_plan_id=new_plan_id,
        diff=diff,
        missions_degraded=sorted(
            solution.degraded, key=lambda mid: (problem.missions[mid].priority, mid)
        ),
        kpi_delta={
            name: float(value) - float(getattr(problem.plan.kpis, name))
            for name, value in metrics.model_dump().items()
        },
        status=solution.status,
    )


# Registration remains deferred until the shared tool registry is implemented.
