"""Replay hard rules and explain unfilled seats in the existing output shape."""

from garuda.contracts.plan import CrewAssignment
from garuda.optimization.schedule_crew.model import CrewSolution
from garuda.optimization.schedule_crew.problem import CrewProblem, duty_windows, pairing_groups
from garuda.optimization.schedule_crew.schemas import ScheduleCrewOutput, SlotGap


def validate_solution(problem: CrewProblem, solution: CrewSolution) -> None:
    """Fail closed before a worker result is written to the draft plan."""
    used = set()
    by_crew: dict[int, list[int]] = {}
    chosen = dict(solution.assignments)
    for i, k in solution.assignments:
        if i in used or not 0 <= i < len(problem.candidates) or k not in problem.candidates[i]:
            raise RuntimeError("crew solver returned a duplicate or ineligible seat assignment")
        used.add(i)
        by_crew.setdefault(k, []).append(i)
    for indices in by_crew.values():
        ordered = sorted(indices, key=lambda i: problem.starts[i])
        if any(problem.starts[j] < problem.ends[i] + problem.min_rest for i, j in zip(ordered, ordered[1:])):
            raise RuntimeError("crew solver violated overlap or minimum rest")
        if any(sum(duration for _, duration in window) > problem.max_duty
               for window in duty_windows(problem, indices)):
            raise RuntimeError("crew solver violated rolling-24h duty")
    for indices in pairing_groups(problem):
        if len({problem.crew[chosen[i]].sqn_id for i in indices if i in chosen}) > 1:
            raise RuntimeError("crew solver violated squadron pairing")


def _gap(problem: CrewProblem, solution: CrewSolution, i: int) -> SlotGap:
    slot = problem.plan.sortie_slots[i]
    if not solution.has_solution:
        return SlotGap(slot_id=slot.slot_id, reason="REST",
                       detail=f"solver returned {solution.status}; no schedule found, eligibility is unresolved")
    candidates = problem.candidates[i]
    if not candidates:
        blockers = problem.blockers[i]
        # Prefer a qualified member's availability reason over unrelated roles.
        relevant = [(k, b) for k, b in blockers.items() if b.reason != "NO_QUALIFIED"]
        if not relevant:
            relevant = list(blockers.items())
        reason = "REST" if any(b.reason == "REST" for _, b in relevant) else (
            "MEDICAL" if any(b.reason == "MEDICAL" for _, b in relevant) else "NO_QUALIFIED"
        )
        details = [f"{problem.crew[k].crew_id}: {b.detail}" for k, b in relevant[:5]]
        return SlotGap(slot_id=slot.slot_id, reason=reason,
                       detail="; ".join(details) or "no crew in the selected bases")

    chosen = dict(solution.assignments)
    group = next((indices for indices in pairing_groups(problem) if i in indices), [])
    partner_sqns = {problem.crew[chosen[j]].sqn_id for j in group if j in chosen}
    details = []
    reasons = []
    for k in candidates:
        member = problem.crew[k]
        if partner_sqns and member.sqn_id not in partner_sqns:
            reasons.append("NO_QUALIFIED")
            details.append(f"{member.crew_id}: squadron does not match the paired seat")
            continue
        indices = [j for j, assigned_k in solution.assignments if assigned_k == k]
        conflicts = [j for j in indices
                     if problem.starts[i] < problem.ends[j] + problem.min_rest
                     and problem.starts[j] < problem.ends[i] + problem.min_rest]
        if conflicts:
            j = conflicts[0]
            gap = (problem.starts[i] - problem.ends[j] if problem.starts[j] <= problem.starts[i]
                   else problem.starts[j] - problem.ends[i])
            missing = (problem.min_rest - gap) / 3_600_000_000
            details.append(f"{member.crew_id}: overlap/rest conflict with "
                           f"{problem.plan.sortie_slots[j].slot_id}, needs {missing:.2f} h more separation")
            reasons.append("REST")
        elif any(sum(duration for _, duration in window) > problem.max_duty
                 for window in duty_windows(problem, indices + [i])):
            details.append(f"{member.crew_id}: rolling-24h duty limit")
            reasons.append("REST")
        else:
            details.append(f"{member.crew_id}: coverage search stopped before assigning this seat")
            reasons.append("REST")
    return SlotGap(slot_id=slot.slot_id,
                   reason="REST" if "REST" in reasons else "NO_QUALIFIED",
                   detail="; ".join(details[:5]))


def explain(problem: CrewProblem, solution: CrewSolution) -> ScheduleCrewOutput:
    validate_solution(problem, solution)
    assignments = [CrewAssignment(crew_id=problem.crew[k].crew_id,
                                  slot_id=problem.plan.sortie_slots[i].slot_id,
                                  role=problem.plan.sortie_slots[i].role)
                   for i, k in solution.assignments]
    filled = {i for i, _ in solution.assignments}
    fatigue = list(problem.fatigue)
    hours = [0.0] * len(problem.crew)
    for i, k in solution.assignments:
        fatigue[k] += problem.increments[i]
        hours[k] += (problem.ends[i] - problem.starts[i]) / 3_600_000_000
    fatigue_after = {member.crew_id: min(1.0, fatigue[k]) for k, member in enumerate(problem.crew)}
    count = len(problem.plan.sortie_slots)
    return ScheduleCrewOutput(
        status=solution.status, crew_assignments=assignments,
        unfilled_slots=[_gap(problem, solution, i) for i in range(count) if i not in filled],
        fatigue_after=fatigue_after,
        kpis={
            "filled_slots": float(len(filled)), "unfilled_slots": float(count - len(filled)),
            "coverage_pct": 100.0 * len(filled) / count if count else 100.0,
            "mean_fatigue": sum(fatigue_after.values()) / len(fatigue_after) if fatigue_after else 0.0,
            "flight_hours_imbalance": max(hours) - min(hours) if hours else 0.0,
            "overload_hours": 0.0,
            "coverage_proven": float(solution.coverage_proven),
        },
    )
