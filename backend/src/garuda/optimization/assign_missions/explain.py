"""Explain solutions through existing output fields and reason codes."""

from garuda.contracts.plan import PlanKPIs, UnassignedMission
from garuda.optimization.assign_missions.validate import validate


def explain(problem, solution):
    chosen = [problem.candidates[k] for k in solution.selected]
    fulfilled = {c.assignment.mission_id for c in chosen}
    unassigned = []
    for mid, mission in problem.missions.items():
        if mid in fulfilled:
            continue
        if solution.status == "INFEASIBLE":
            reason, detail = "NO_AIRCRAFT", solution.detail
        elif any(
            dep not in fulfilled | problem.completed for dep in mission.dependencies
        ):
            tanker = any(
                problem.missions[dep].type == "AAR"
                for dep in mission.dependencies
                if dep in problem.missions
            )
            reason, detail = (
                "NO_TANKER" if tanker else "NO_AIRCRAFT",
                "Required prerequisite/support mission was not fulfilled",
            )
        else:
            candidates = [
                k
                for k, c in problem.candidates.items()
                if c.assignment.mission_id == mid
            ]
            if not candidates:
                # Buckets outside the mission window are not the useful blocker
                # when all time-feasible tuples fail another eligibility gate.
                failures = problem.rejected[mid]
                actionable = {k: n for k, n in failures.items() if k[0] != "WINDOW"}
                reason, detail = max(
                    actionable or failures, key=lambda k: (actionable or failures)[k]
                )
            else:
                conflicts = []
                for key in candidates:
                    conflicts.extend(
                        validate(problem, [*solution.selected, key], partial=True)
                    )
                reason = "NO_AIRCRAFT"
                for prefix, code in (
                    ("Resource stock:", "NO_WEAPON_STOCK"),
                    ("Base capacity:", "BASE_CAPACITY"),
                    ("No feasible crew", "NO_QUALIFIED_CREW"),
                    ("Aircraft turnaround:", "NO_AIRCRAFT"),
                ):
                    if any(c.startswith(prefix) for c in conflicts):
                        reason = code
                        break
                detail = (
                    "; ".join(sorted(set(conflicts))[:3])
                    if conflicts
                    else "Insufficient distinct aircraft for the full package, or displaced by the weighted objective"
                )
        unassigned.append(
            UnassignedMission(mission_id=mid, reason=reason, detail=detail)
        )
    kpis = PlanKPIs(
        value=sum(problem.missions[mid].value for mid in fulfilled),
        fulfilment_pct=len(fulfilled) / len(problem.missions)
        if problem.missions
        else 0.0,
        sorties=len(chosen),
        risk=sum(c.risk for c in chosen),
    )
    return kpis, unassigned, sorted(set(solution.binding))
