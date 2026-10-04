"""explain(problem, decision, new_plan_id) -> (PlanDraft, PlanDiff). See docs/03_optimization_layer/rl_retasking_agent.md.

A filled displaced slot is a change, an unfilled one is a removal, and a filled new-mission slot is
an addition. Unfilled slots are listed as unassigned with the reason no candidate fit, or
NO_AIRCRAFT when candidates fit but were all taken.
"""

from garuda.contracts.geo import Waypoint
from garuda.contracts.plan import (
    Assignment,
    FlightLeg,
    PlanDiff,
    PlanDraft,
    PlanKPIs,
    UnassignedMission,
)
from garuda.sim.rl_retask.decisions import Decision
from garuda.sim.rl_retask.problem import RetaskProblem

CRUISE_ALT_FT = 25_000


def explain(problem: RetaskProblem, decision: Decision, new_plan_id: str) -> tuple[PlanDraft, PlanDiff]:
    plan = problem.plan
    diff = PlanDiff()
    added: list[Assignment] = []
    legs: list[FlightLeg] = []
    unassigned: list[UnassignedMission] = []
    for s, (slot, choice) in enumerate(zip(problem.slots, decision)):
        if choice is None:
            if slot.old is not None:
                diff.removed.append(slot.old)
            reason = problem.reasons[s] if not problem.instance.feasible[s].any() else "NO_AIRCRAFT"
            unassigned.append(UnassignedMission(mission_id=slot.mission_id, reason=reason, detail=f"rl_retask after {problem.event.type}"))
            continue
        candidate = problem.candidates[choice]
        new = Assignment(
            tail_no=candidate.tail_no,
            config_id=problem.config[s][choice],
            mission_id=slot.mission_id,
            depart=problem.depart[s][choice],
            tot=slot.tot,
        )
        added.append(new)
        if slot.old is not None:
            diff.changed.append((slot.old, new))
        else:
            diff.added.append(new)
        point = problem.target_points[s]
        if point is not None:
            legs.append(
                FlightLeg(
                    mission_id=slot.mission_id,
                    tail_no=candidate.tail_no,
                    waypoints=[
                        Waypoint(lat=candidate.lat, lon=candidate.lon, alt_ft=CRUISE_ALT_FT, eta=new.depart),
                        Waypoint(lat=point[0], lon=point[1], alt_ft=CRUISE_ALT_FT, eta=new.tot),
                    ],
                    needs_aar=slot.needs_aar and problem.tanker_available,
                )
            )

    assignments = problem.kept + added
    removed_keys = {(a.mission_id, a.tail_no) for a in problem.displaced}
    covered = {a.mission_id for a in assignments}
    open_missions = {u.mission_id for u in plan.unassigned + unassigned} - covered
    kept_slots = [slot for slot in plan.sortie_slots if (slot.mission_id, slot.tail_no) not in removed_keys]
    kept_slot_ids = {slot.slot_id for slot in kept_slots}
    new_plan = PlanDraft(
        plan_id=new_plan_id,
        parent_plan_id=plan.plan_id,
        scenario_time=problem.event.time,
        assignments=assignments,
        unassigned=list(
            {u.mission_id: u for u in reversed(plan.unassigned + unassigned) if u.mission_id not in covered}.values()
        )[::-1],
        sortie_slots=kept_slots,
        crew_assignments=[c for c in plan.crew_assignments if c.slot_id in kept_slot_ids],
        legs=[leg for leg in plan.legs if (leg.mission_id, leg.tail_no) not in removed_keys] + legs,
        kpis=PlanKPIs(
            value=sum(problem.mission_values.get(m, 0.0) for m in covered),
            fulfilment_pct=100.0 * len(covered) / max(1, len(covered | open_missions)),
            sorties=len(assignments),
            risk=plan.kpis.risk,
        ),
    )
    return new_plan, diff
