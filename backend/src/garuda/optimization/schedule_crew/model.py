"""Pure, synchronous CP-SAT solve; no context ports or writes in the worker."""

from dataclasses import dataclass
from math import ceil

from garuda.optimization.schedule_crew.problem import CrewProblem, duty_windows, pairing_groups


@dataclass(frozen=True)
class CrewSolution:
    status: str
    # (slot index, crew index)
    assignments: tuple[tuple[int, int], ...]
    has_solution: bool
    coverage_proven: bool


def solve(problem: CrewProblem, options=None) -> CrewSolution:
    if not problem.plan.sortie_slots:
        return CrewSolution("OPTIMAL", (), True, True)
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise RuntimeError("schedule_crew requires the existing optimization extra: pip install 'garuda[opt]'") from exc

    model = cp_model.CpModel()
    assigned = {(k, i): model.NewBoolVar(f"crew_{k}_slot_{i}")
                for i, candidates in enumerate(problem.candidates) for k in candidates}
    gaps = []
    for i, candidates in enumerate(problem.candidates):
        gap = model.NewBoolVar(f"gap_{i}")
        model.AddExactlyOne([assigned[k, i] for k in candidates] + [gap])
        gaps.append(gap)

    loads = []
    fatigue_terms = []
    max_load = sum(ceil((end - start) / 1_000_000) for start, end in zip(problem.starts, problem.ends))
    for k in range(len(problem.crew)):
        indices = [i for i in range(len(problem.candidates)) if (k, i) in assigned]
        if not indices:
            continue
        intervals = [model.NewOptionalIntervalVar(
            problem.starts[i], problem.ends[i] - problem.starts[i] + problem.min_rest,
            problem.ends[i] + problem.min_rest, assigned[k, i], f"duty_{k}_{i}",
        ) for i in indices]
        model.AddNoOverlap(intervals)
        for window in duty_windows(problem, indices):
            if sum(duration for _, duration in window) > problem.max_duty:
                model.Add(sum(duration * assigned[k, i] for i, duration in window) <= problem.max_duty)
        load = model.NewIntVar(0, max_load, f"flight_seconds_{k}")
        model.Add(load == sum(ceil((problem.ends[i] - problem.starts[i]) / 1_000_000) * assigned[k, i]
                              for i in indices))
        loads.append(load)
        fatigue_terms.extend(round(10_000 * (problem.fatigue[k] + problem.increments[i])) * assigned[k, i]
                             for i in indices)

    for group_index, indices in enumerate(pairing_groups(problem)):
        squadrons = sorted({problem.crew[k].sqn_id for i in indices for k in problem.candidates[i]})
        selected = {sqn: model.NewBoolVar(f"pair_{group_index}_{sqn}") for sqn in squadrons}
        model.Add(sum(selected.values()) <= 1)
        for i in indices:
            for k in problem.candidates[i]:
                model.Add(assigned[k, i] <= selected[problem.crew[k].sqn_id])

    # One missing seat costs more than the entire secondary objective can cost.
    # This gives maximum coverage first, then lower fatigue and balanced flying.
    fatigue_bound = sum(max((round(10_000 * (problem.fatigue[k] + problem.increments[i]))
                             for k in candidates), default=0)
                        for i, candidates in enumerate(problem.candidates))
    secondary = sum(fatigue_terms)
    if loads:
        highest = model.NewIntVar(0, max_load, "highest_load")
        lowest = model.NewIntVar(0, max_load, "lowest_load")
        model.AddMaxEquality(highest, loads)
        model.AddMinEquality(lowest, loads)
        secondary += highest - lowest
    model.Minimize((fatigue_bound + max_load + 1) * sum(gaps) + secondary)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = problem.time_limit_s
    solver.parameters.random_seed = problem.seed % (2**31 - 1)
    solver.parameters.num_search_workers = 1
    status = solver.Solve(model)
    if status == cp_model.MODEL_INVALID:
        raise RuntimeError(f"invalid crew scheduling model: {model.Validate()}")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return CrewSolution(solver.StatusName(status), (), False, False)
    assignments = tuple(sorted((i, k) for (k, i), variable in assigned.items() if solver.Value(variable)))
    has_gaps = len(assignments) < len(problem.plan.sortie_slots)
    proven = status == cp_model.OPTIMAL or not has_gaps
    # INFEASIBLE refers to complete coverage. The returned partial schedule still
    # satisfies every hard rule. A time limit alone never proves infeasibility.
    result_status = "INFEASIBLE" if has_gaps and proven else solver.StatusName(status)
    return CrewSolution(result_status, assignments, True, proven)
