"""Priority greedy fallback; candidate plans are independently validated."""

from garuda.optimization.assign_missions.model import Solution
from garuda.optimization.assign_missions.validate import crew_witness, validate


def baseline(problem):
    if problem.lock_errors or problem.extensions:
        return None
    selected = sorted(problem.lock_keys)
    if validate(problem, selected, partial=True):
        return None
    done, visiting = set(problem.completed), set()

    def assign(mid):
        nonlocal selected
        if mid in done:
            return True
        if mid in visiting or mid not in problem.missions:
            return False
        visiting.add(mid)
        mission = problem.missions[mid]
        if any(not assign(dep) for dep in mission.dependencies):
            visiting.remove(mid)
            return False
        trial = list(selected)
        existing = sum(
            problem.candidates[k].assignment.mission_id == mid for k in trial
        )
        choices = [
            k
            for k, c in problem.candidates.items()
            if c.assignment.mission_id == mid and k not in trial
        ]
        choices.sort(
            key=lambda k: (
                -(
                    problem.effect_weight * problem.candidates[k].pk
                    - problem.risk_weight * problem.candidates[k].risk
                ),
                problem.candidates[k].assignment.depart,
                k,
            )
        )
        for key in choices:
            if existing >= mission.min_aircraft:
                break
            if not validate(problem, [*trial, key], partial=True):
                trial.append(key)
                existing += 1
        visiting.remove(mid)
        if existing < mission.min_aircraft or validate(problem, trial):
            return False
        selected = trial
        done.add(mid)
        return True

    for mid in sorted(
        problem.missions, key=lambda mid: (-problem.missions[mid].value, mid)
    ):
        assign(mid)
    if validate(problem, selected):
        return None
    crew = crew_witness([problem.candidates[k] for k in selected])
    return Solution("FEASIBLE", selected, crew or {})
