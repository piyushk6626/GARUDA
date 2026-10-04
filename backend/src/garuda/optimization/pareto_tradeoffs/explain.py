"""Deterministic medoid representatives, a knee recommendation and plan diffs."""

import json
from hashlib import sha256

import numpy as np

from garuda.contracts.plan import CrewAssignment, PlanDraft, PlanKPIs, UnassignedMission
from garuda.optimization.pareto_tradeoffs.model import losses
from garuda.optimization.pareto_tradeoffs.problem import (
    assignment_key,
    crew_group,
    sortie_slot,
)
from garuda.optimization.pareto_tradeoffs.schemas import (
    ParetoTradeoffsOutput,
    PlanOption,
)


def representatives(points, names, count):
    vectors = np.array([losses(p.objectives, names) for p in points])
    spans = np.ptp(vectors, axis=0)
    normalized = (vectors - vectors.min(axis=0)) / np.where(spans > 1e-9, spans, 1)
    first = int(np.argmin(normalized[:, 0]))
    last = int(np.argmax(normalized[:, 0]))
    line = normalized[last] - normalized[first]
    length = float(line @ line)
    if length > 1e-12:
        projection = np.clip((normalized - normalized[first]) @ line / length, 0, 1)
        distance = np.linalg.norm(
            normalized - normalized[first] - projection[:, None] * line, axis=1
        )
    else:
        distance = np.zeros(len(points))
    knee = (
        int(np.argmax(distance))
        if distance.max() > 1e-9
        else int(np.argmin(np.linalg.norm(normalized, axis=1)))
    )
    target = min(count, len(points))
    # Keep the knee and, when capacity permits, extremes; cluster the rest.
    medoids = [knee]
    for i in [
        first,
        last,
        *[int(np.argmin(normalized[:, j])) for j in range(len(names))],
    ]:
        if i not in medoids and len(medoids) < target:
            medoids.append(i)
    fixed = len(medoids)
    distances = np.linalg.norm(normalized[:, None] - normalized[None, :], axis=2)
    while len(medoids) < target:
        scores = distances[:, medoids].min(axis=1)
        scores[medoids] = -1
        medoids.append(int(np.argmax(scores)))
    for _ in range(20):
        clusters = np.argmin(distances[:, medoids], axis=1)
        updated = medoids.copy()
        for j in range(fixed, len(medoids)):
            members = np.flatnonzero(clusters == j)
            if len(members):
                updated[j] = int(
                    members[np.argmin(distances[np.ix_(members, members)].sum(axis=1))]
                )
        if updated == medoids:
            break
        medoids = updated
    return medoids, knee


def draft(problem, point):
    candidates = [problem.assignment.candidates[k] for k in point.selected]
    assignments = sorted(
        (c.assignment.model_copy(deep=True) for c in candidates), key=assignment_key
    )
    slots, crews = [], []
    for c in sorted(candidates, key=lambda c: assignment_key(c.assignment)):
        for role in sorted(c.crew):
            members = sorted(point.crew[crew_group(c, role)])
            for seat, member in enumerate(members):
                slot = sortie_slot(c, role, seat)
                slots.append(slot)
                crews.append(
                    CrewAssignment(crew_id=member, slot_id=slot.slot_id, role=role)
                )
    fulfilled = {a.mission_id for a in assignments}
    result = PlanDraft(
        plan_id="",
        parent_plan_id=problem.base.plan_id,
        scenario_time=problem.base.scenario_time,
        assignments=assignments,
        sortie_slots=slots,
        crew_assignments=crews,
        unassigned=[
            UnassignedMission(
                mission_id=mid,
                reason="NO_AIRCRAFT",
                detail="Omitted by the selected Pareto trade-off",
            )
            for mid in sorted(problem.assignment.missions)
            if mid not in fulfilled
        ],
        kpis=PlanKPIs(
            value=sum(problem.assignment.missions[mid].value for mid in fulfilled),
            risk=sum(c.risk for c in candidates),
            sorties=len(assignments),
            fulfilment_pct=len(fulfilled) / len(problem.assignment.missions)
            if problem.assignment.missions
            else 0,
        ),
    )
    content = json.dumps(
        result.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    result.plan_id = "pareto-" + sha256(content.encode()).hexdigest()[:24]
    return result


def explain(problem, solution):
    indices, knee = representatives(
        solution.points, problem.objectives, problem.n_points
    )
    labels = {knee: "Balanced"}
    for name, label in (
        ("value", "Max effect"),
        ("risk", "Min risk"),
        ("disruption", "Min disruption"),
    ):
        if name in problem.objectives:
            best = min(
                indices,
                key=lambda i: (
                    -solution.points[i].objectives[name]
                    if name == "value"
                    else solution.points[i].objectives[name],
                    i,
                ),
            )
            labels.setdefault(best, label)
    original = {assignment_key(a) for a in problem.base.assignments}
    options = []
    for index in indices:
        point = solution.points[index]
        plan = draft(problem, point)
        current = {assignment_key(a) for a in plan.assignments}
        differences = [
            f"{len(current - original)} added and {len(original - current)} removed assignments vs base plan"
        ]
        if "value" in problem.objectives and "risk" in problem.objectives:
            others = [
                p
                for p in solution.points
                if p.objectives["value"] < point.objectives["value"]
            ]
            if others:
                neighbour = max(others, key=lambda p: p.objectives["value"])
                differences.append(
                    f"+{point.objectives['value'] - neighbour.objectives['value']:.6g} value costs "
                    f"{point.objectives['risk'] - neighbour.objectives['risk']:+.6g} risk vs lower-value option"
                )
        if index == knee:
            differences.append("Recommended knee of the normalized sampled frontier")
        differences.extend(solution.notes)
        options.append(
            PlanOption(
                plan_id=plan.plan_id,
                label=labels.get(index, f"Option {len(options) + 1}"),
                objectives=point.objectives,
                key_differences=differences,
            )
        )
    return ParetoTradeoffsOutput(
        options=options,
        ideal=solution.ideal,
        nadir=solution.nadir,
        recommendation=labels[knee],
    )
