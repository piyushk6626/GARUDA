"""Deterministic assignment diffs, KPIs and disruption narratives."""

from collections import defaultdict

from garuda.contracts.plan import PlanDiff, PlanKPIs
from garuda.contracts.results import Explanation
from garuda.optimization.retask_rolling_horizon.problem import assignment_key


def plan_diff(old, new) -> PlanDiff:
    old_keys = {assignment_key(a) for a in old}
    new_keys = {assignment_key(a) for a in new}
    removed, added = defaultdict(list), defaultdict(list)
    for a in old:
        if assignment_key(a) not in new_keys:
            removed[a.mission_id].append(a)
    for a in new:
        if assignment_key(a) not in old_keys:
            added[a.mission_id].append(a)
    diff = PlanDiff()
    for mid in sorted(removed.keys() | added.keys()):
        before = sorted(removed[mid], key=assignment_key)
        after = sorted(added[mid], key=assignment_key)
        # Prefer same-tail pairs to explain rescheduling/configuration changes.
        for a in list(before):
            replacement = next((b for b in after if b.tail_no == a.tail_no), None)
            if replacement is not None:
                diff.changed.append((a, replacement))
                before.remove(a)
                after.remove(replacement)
        count = min(len(before), len(after))
        diff.changed.extend(zip(before[:count], after[:count]))
        diff.removed.extend(before[count:])
        diff.added.extend(after[count:])
    return diff


def kpis(problem, solution) -> PlanKPIs:
    counts = defaultdict(int)
    for a in solution.assignments:
        counts[a.mission_id] += 1
    fulfilled = {
        mid
        for mid, count in counts.items()
        if count >= problem.missions[mid].min_aircraft
    }
    considered = (
        {c.assignment.mission_id for c in problem.candidates}
        | set(problem.reasons)
        | problem.affected
        | set(counts)
    )
    return PlanKPIs(
        value=sum(problem.missions[mid].value for mid in fulfilled),
        fulfilment_pct=100 * len(fulfilled) / len(considered) if considered else 100.0,
        sorties=len(solution.assignments),
        # Update the active part of the parent's aggregate risk. Completed
        # sorties have no per-assignment risk field and retain their contribution.
        risk=max(
            0.0,
            problem.plan.kpis.risk
            + sum(c.risk for c in solution.selected)
            - sum(problem.old_risk.values()),
        ),
    )


def explain(problem, solution) -> Explanation:
    if solution.status in {"INFEASIBLE", "ERROR"}:
        return Explanation(
            summary="No feasible repair was found with frozen assignments preserved.",
            binding_constraints=sorted(problem.frozen_conflicts),
            notes=[
                f"{mid}: {problem.reasons.get(mid, 'frozen resource conflict')}"
                for mid in sorted(solution.degraded)
            ],
        )
    diff = plan_diff(problem.plan.assignments, solution.assignments)
    metrics = kpis(problem, solution).model_dump()
    notes = [
        f"{before.mission_id}: {before.tail_no} replaced by {after.tail_no}; "
        f"departure {before.depart.isoformat()} -> {after.depart.isoformat()}"
        for before, after in diff.changed
    ]
    notes.extend(f"{a.mission_id}: added {a.tail_no}" for a in diff.added)
    notes.extend(f"{a.mission_id}: removed {a.tail_no}" for a in diff.removed)
    notes.extend(
        f"{mid}: route must be replanned after new SAM"
        for mid in sorted(problem.route_replan)
    )
    if solution.widened:
        notes.append("Expanded the restricted neighbourhood to a full solve.")
    return Explanation(
        summary=f"Repaired plan with {len(diff.changed)} changed, {len(diff.added)} added and {len(diff.removed)} removed assignments.",
        kpis={name: float(value) for name, value in metrics.items()},
        notes=notes,
    )
