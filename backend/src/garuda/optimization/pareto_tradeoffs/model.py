"""Bounded Pareto searches over the shared assignment MILP."""

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import product
from math import isfinite
from time import perf_counter

import numpy as np

from garuda.contracts.tool import ToolInputError
from garuda.optimization.assign_missions.model import build_model, variable
from garuda.optimization.assign_missions.validate import validate
from garuda.optimization.pareto_tradeoffs.problem import assignment_key, crew_group


@dataclass
class Point:
    selected: list[tuple]
    crew: dict[tuple, list[str]]
    objectives: dict[str, float]
    hints: dict[str, float] = field(default_factory=dict)


@dataclass
class Solution:
    points: list[Point]
    ideal: dict[str, float]
    nadir: dict[str, float]
    notes: list[str] = field(default_factory=list)


def losses(vector, names):
    return np.array([-vector[n] if n == "value" else vector[n] for n in names])


def nondominated(points, names):
    unique = {}
    for point in points:
        unique.setdefault(tuple(point.objectives[n] for n in names), point)
    candidates = list(unique.values())
    vectors = [losses(p.objectives, names) for p in candidates]
    return [
        p
        for i, p in enumerate(candidates)
        if not any(
            np.all(v <= vectors[i]) and np.any(v < vectors[i])
            for j, v in enumerate(vectors)
            if j != i
        )
    ]


def evaluate(problem, selected, crew):
    candidates = [problem.assignment.candidates[k] for k in selected]
    fulfilled = {c.assignment.mission_id for c in candidates}
    original = {assignment_key(a) for a in problem.base.assignments}
    current = {assignment_key(c.assignment) for c in candidates}
    accumulated = defaultdict(float)
    for group, members in crew.items():
        for member in members:
            accumulated[member] += problem.increments.get(group, 0.0)
    vector = {
        "value": sum(problem.assignment.missions[mid].value for mid in fulfilled),
        "risk": sum(c.risk for c in candidates),
        "munitions": sum(problem.munitions[k] for k in selected),
        "fatigue": max(
            (
                min(1.0, problem.fatigue.get(m, 0.0) + inc)
                for m, inc in accumulated.items()
            ),
            default=0.0,
        ),
        "disruption": float(len(original ^ current)),
    }
    return {n: float(vector[n]) for n in problem.objectives}


def _expressions(problem, lp, x, y, crew_vars):
    import pulp

    p = problem.assignment
    original = {assignment_key(a) for a in problem.base.assignments}
    represented = {assignment_key(c.assignment) for c in p.candidates.values()}
    expressions = {
        "value": pulp.lpSum(m.value * y[mid] for mid, m in p.missions.items()),
        "risk": pulp.lpSum(c.risk * x[k] for k, c in p.candidates.items()),
        "munitions": pulp.lpSum(problem.munitions[k] * x[k] for k in x),
        "disruption": pulp.lpSum(
            1 - x[k] if assignment_key(c.assignment) in original else x[k]
            for k, c in p.candidates.items()
        )
        + len(original - represented),
    }
    if "fatigue" in problem.objectives:
        members = defaultdict(list)
        for g, choices in crew_vars.items():
            for member, choice in choices.items():
                members[member].append((g, choice))
        scores = []
        for i, (member, entries) in enumerate(sorted(members.items())):
            active = variable(lp, f"pareto_active_{i}")
            lp += active <= pulp.lpSum(v for _, v in entries)
            for _, v in entries:
                lp += active >= v
            raw = problem.fatigue[member] * active + pulp.lpSum(
                problem.increments[g] * v for g, v in entries
            )
            upper = max(
                1.0,
                problem.fatigue[member]
                + sum(problem.increments[g] for g, _ in entries),
            )
            saturated = variable(lp, f"pareto_saturated_{i}")
            score = variable(lp, f"pareto_fatigue_{i}", binary=False, upper=1)
            lp += score <= raw
            lp += score >= raw - upper * saturated
            lp += score >= saturated
            scores.append(score)
        peak = variable(lp, "pareto_max_fatigue", binary=False, upper=1)
        selectors = []
        for i, score in enumerate(scores):
            selector = variable(lp, f"pareto_peak_{i}")
            lp += peak >= score
            lp += peak <= score + 1 - selector
            selectors.append(selector)
        lp += pulp.lpSum(selectors) == 1 if selectors else peak == 0
        expressions["fatigue"] = peak
    return {n: expressions[n] for n in problem.objectives}


def _solver(seconds, seed, warm_start=False):
    import pulp

    highs = pulp.HiGHS(
        msg=False,
        timeLimit=seconds,
        gapRel=0,
        threads=1,
        random_seed=seed % (2**31 - 1),
        warmStart=warm_start,
    )
    if highs.available():
        return highs
    for name in ("PULP_CBC_CMD", "COIN_CMD"):
        factory = getattr(pulp, name, None)
        if factory is not None:
            solver = factory(
                msg=False,
                timeLimit=seconds,
                gapRel=0,
                threads=1,
                warmStart=warm_start,
                options=[f"randomSeed {seed % (2**31 - 1)}"],
            )
            if solver.available():
                return solver
    raise ToolInputError("pareto_tradeoffs requires HiGHS or CBC; install garuda[opt]")


def _attempt(problem, objective, bounds, ideal, scales, warm, deadline, notes):
    import pulp

    seconds = min(problem.time_limit_s, deadline - perf_counter())
    if seconds <= 0:
        return None
    job_deadline = perf_counter() + seconds
    lp, x, y, crew_vars, _ = build_model(problem.assignment)
    expressions = _expressions(problem, lp, x, y, crew_vars)
    norm = {
        n: ((-expressions[n] if n == "value" else expressions[n]) - ideal[n])
        / scales[n]
        for n in problem.objectives
    }
    for n, limit in bounds.items():
        lp += norm[n] <= limit + 1e-8
    primary = pulp.lpSum(weight * norm[n] for n, weight in objective.items())
    lp.sense = pulp.LpMinimize
    lp.setObjective(primary)
    for v in lp.variables():
        if warm is not None and v.name in warm.hints:
            v.setInitialValue(warm.hints[v.name])

    def run():
        remaining = job_deadline - perf_counter()
        if remaining <= 0:
            return None
        stats = lp.solve(
            _solver(
                remaining,
                problem.seed,
                any(v.value() is not None for v in lp.variables()),
            )
        )
        status = int(stats.status) if hasattr(stats, "status") else lp.status
        if status == -1:
            notes.append("Infeasible epsilon/anchor solve skipped")
            return None
        if not lp.valid(1e-6) or any(
            v.value() is None or not isfinite(v.value()) or not v.valid(1e-6)
            for v in lp.variables()
        ):
            notes.append("Solve produced no valid incumbent; skipped")
            return None
        if hasattr(stats, "has_solution") and not stats.has_solution:
            notes.append("Solve produced no incumbent; skipped")
            return None
        selected = [k for k, v in x.items() if v.value() > 0.5]
        crew = {
            g: [m for m, v in entries.items() if v.value() > 0.5]
            for g, entries in crew_vars.items()
            if any(v.value() > 0.5 for v in entries.values())
        }
        errors = validate(problem.assignment, selected, crew)
        if errors:
            raise RuntimeError("Pareto plan failed validation: " + "; ".join(errors))
        optimal = status == 1
        if hasattr(lp, "solverModel") and hasattr(lp.solverModel, "getInfo"):
            gap = lp.solverModel.getInfo().mip_gap
            optimal &= isfinite(gap) and gap <= 1e-7
        elif not hasattr(stats, "has_solution"):
            optimal &= lp.sol_status == pulp.LpSolutionOptimal
        if not optimal:
            notes.append(
                "Time-limited feasible plans; frontier and bounds are approximate"
            )
        return Point(
            selected,
            crew,
            evaluate(problem, selected, crew),
            {v.name: v.value() for v in lp.variables()},
        )

    first = run()
    if first is None:
        return None
    # Lexicographic AUGMECON preserves the primary optimum, then maximizes
    # normalized epsilon slacks, removing weakly efficient solutions.
    lp += primary <= pulp.value(primary) + 1e-8
    lp.setObjective(pulp.lpSum(norm.values()))
    second = run()
    return second or first


def _epsilon_grid(names, count):
    levels = min(25, max(5, count))
    size = levels ** len(names)
    budget = min(size, max(5, min(128, count * 4)))
    indices = sorted(
        {round(i * (size - 1) / max(1, budget - 1)) for i in range(budget)}
    )
    for index in indices:
        digits = []
        for _ in names:
            digits.append((index % levels) / (levels - 1))
            index //= levels
        yield dict(zip(names, digits))


def _weights(names):
    yield dict.fromkeys(names, 1 / len(names))
    for parts in product(range(5), repeat=len(names)):
        if sum(parts) == 4:
            yield {n: w / 4 for n, w in zip(names, parts)}


def solve(problem, options=None) -> Solution:
    try:
        import pulp  # noqa: F401
    except ImportError as exc:
        raise ToolInputError(
            "pareto_tradeoffs requires garuda[opt] (PuLP and HiGHS/CBC)"
        ) from exc
    if problem.method == "nsga2":
        try:
            import pymoo  # noqa: F401
        except ImportError as exc:
            raise ToolInputError(
                "method='nsga2' requires the optional garuda[moo] extra"
            ) from exc
    deadline = perf_counter() + 110  # Existing ToolSpec timeout is 120s.
    names, notes, points = problem.objectives, list(problem.notes), []
    zero, unit = dict.fromkeys(names, 0.0), dict.fromkeys(names, 1.0)
    for name in names:
        point = _attempt(
            problem,
            {name: 1},
            {},
            zero,
            unit,
            points[-1] if points else None,
            deadline,
            notes,
        )
        if point is not None:
            points.append(point)
    if not points:
        raise ToolInputError(
            "No feasible Pareto plan found; increase time_limit_s_per_solve"
        )
    matrix = np.array([losses(p.objectives, names) for p in points])
    lo, hi = matrix.min(axis=0), matrix.max(axis=0)
    ideal = dict(zip(names, lo))
    scales = dict(
        zip(names, np.where(hi - lo > 1e-9, hi - lo, np.maximum(1, np.abs(lo))))
    )
    if len(names) > 1:
        if problem.method == "nsga2":
            points.extend(_nsga2(problem, points, deadline, notes))
        else:
            primary = "value" if "value" in names else names[0]
            constrained = [
                n for i, n in enumerate(names) if n != primary and hi[i] - lo[i] > 1e-9
            ]
            jobs = (
                (
                    ({primary: 1}, b)
                    for b in _epsilon_grid(constrained, problem.n_points)
                )
                if problem.method == "epsilon"
                else ((w, {}) for w in _weights(names))
            )
            for weights, bounds in jobs:
                if perf_counter() >= deadline:
                    notes.append(
                        "Tool time budget exhausted; sampled frontier is approximate"
                    )
                    break
                point = _attempt(
                    problem, weights, bounds, ideal, scales, points[-1], deadline, notes
                )
                if point is not None:
                    points.append(point)
    frontier = nondominated(points, names)
    vectors = np.array([losses(p.objectives, names) for p in frontier])
    low, high = vectors.min(axis=0), vectors.max(axis=0)
    return Solution(
        frontier,
        {n: float(-low[i] if n == "value" else low[i]) for i, n in enumerate(names)},
        {n: float(-high[i] if n == "value" else high[i]) for i, n in enumerate(names)},
        sorted(set(notes)),
    )


def _nsga2(problem, anchors, deadline, notes):
    """Binary assignment and crew genomes; every retained point is validated."""
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.operators.crossover.ux import UniformCrossover
    from pymoo.operators.mutation.bitflip import BitflipMutation
    from pymoo.optimize import minimize

    keys, groups = list(problem.assignment.candidates), {}
    for c in problem.assignment.candidates.values():
        for role in c.crew:
            g = crew_group(c, role)
            groups[g] = g[5]
    seats = [(g, m) for g, eligible in groups.items() for m in eligible]
    size = len(keys) + len(seats)
    if not size:
        return []
    archive = []

    class Search(ElementwiseProblem):
        def __init__(self):
            super().__init__(
                n_var=size,
                n_obj=len(problem.objectives),
                n_ieq_constr=1,
                xl=0,
                xu=1,
                vtype=bool,
            )

        def _evaluate(self, genome, out, *args, **kwargs):
            selected = [k for k, bit in zip(keys, genome[: len(keys)]) if bit]
            active = {
                crew_group(problem.assignment.candidates[k], role)
                for k in selected
                for role in problem.assignment.candidates[k].crew
            }
            crew = {g: [] for g in active}
            for (g, member), bit in zip(seats, genome[len(keys) :]):
                if bit and g in crew:
                    crew[g].append(member)
            errors = validate(problem.assignment, selected, crew)
            vector = evaluate(problem, selected, crew)
            out["F"], out["G"] = losses(vector, problem.objectives), [len(errors)]
            if not errors:
                archive.append(Point(selected, crew, vector))

    rng = np.random.default_rng(problem.seed % (2**32))
    sampling = rng.random((100, size)) < 0.1
    for i, point in enumerate(anchors[:100]):
        selected = set(point.selected)
        sampling[i] = [k in selected for k in keys] + [
            m in point.crew.get(g, []) for g, m in seats
        ]

    def callback(algorithm):
        if perf_counter() >= deadline:
            algorithm.termination.force_termination = True

    minimize(
        Search(),
        NSGA2(
            pop_size=100,
            sampling=sampling,
            crossover=UniformCrossover(),
            mutation=BitflipMutation(),
            eliminate_duplicates=True,
        ),
        ("n_gen", 50),
        seed=problem.seed % (2**32),
        verbose=False,
        callback=callback,
    )
    notes.append("NSGA-II sampled frontier: population 100, up to 50 generations")
    return nondominated(archive, problem.objectives)
