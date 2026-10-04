"""Prepare an advisory child and exact assignment diff."""

from uuid import uuid4

from garuda.contracts.plan import PlanDraft, UnassignedMission
from garuda.optimization.retask_rolling_horizon.explain import kpis, plan_diff
from garuda.optimization.retask_rolling_horizon.problem import assignment_key


def explain(problem, solution):
    p = problem.retask
    unchanged = {assignment_key(a) for a in p.plan.assignments} & {
        assignment_key(a) for a in solution.assignments
    }
    changed_pairs = {
        (a.mission_id, a.tail_no)
        for a in p.plan.assignments + solution.assignments
        if assignment_key(a) not in unchanged
    }
    unchanged_pairs = {(key[2], key[0]) for key in unchanged} - changed_pairs
    slots = [
        s for s in p.plan.sortie_slots if (s.mission_id, s.tail_no) in unchanged_pairs
    ]
    slot_ids = {s.slot_id for s in slots}
    fulfilled = {a.mission_id for a in solution.assignments}
    considered = (
        {c.assignment.mission_id for c in p.candidates} | set(p.reasons) | p.affected
    )
    unassigned = {
        u.mission_id: u.model_copy(deep=True)
        for u in p.plan.unassigned
        if u.mission_id not in fulfilled
    }
    for mid in sorted(considered - fulfilled):
        unassigned[mid] = UnassignedMission(
            mission_id=mid,
            reason=p.reasons.get(mid, "NO_AIRCRAFT"),
            detail="Unassigned after validated advisory retasking; downstream planning may be required.",
        )
    draft = PlanDraft(
        plan_id=f"plan-{uuid4().hex}",
        parent_plan_id=p.plan.plan_id,
        scenario_time=problem.now,
        status="DRAFT",
        assignments=solution.assignments,
        unassigned=[unassigned[mid] for mid in sorted(unassigned)],
        sortie_slots=slots,
        crew_assignments=[c for c in p.plan.crew_assignments if c.slot_id in slot_ids],
        legs=[
            leg
            for leg in p.plan.legs
            if (leg.mission_id, leg.tail_no) in unchanged_pairs
            and leg.mission_id not in p.route_replan
        ],
        kpis=kpis(p, solution),
    ).model_copy(deep=True)
    return draft, plan_diff(p.plan.assignments, draft.assignments)
