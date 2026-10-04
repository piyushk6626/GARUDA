"""Sparse PuLP MILP. Solver objects stay off the async loop."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from math import isfinite

from garuda.contracts.tool import ToolInputError


@dataclass
class Solution:
    status: str
    selected: list[tuple] = field(default_factory=list)
    crew: dict[tuple, list[str]] = field(default_factory=dict)
    binding: list[str] = field(default_factory=list)
    detail: str = ""


def constraints(model):
    return (
        model.constraints()
        if callable(model.constraints)
        else model.constraints.values()
    )


def variable(model, name, *, binary=True, upper=None):
    import pulp

    kwargs = {"cat": "Binary"} if binary else {"lowBound": 0, "upBound": upper}
    if hasattr(model, "add_variable"):
        return model.add_variable(name, **kwargs)
    return pulp.LpVariable(name, **kwargs)


def solver_factory(seconds, seed):
    import pulp

    highs = pulp.HiGHS(
        msg=False, timeLimit=seconds, gapRel=0.01, threads=1, random_seed=seed
    )
    if highs.available():
        return highs
    cbc_class = getattr(pulp, "PULP_CBC_CMD", None) or pulp.COIN_CMD
    # Legacy CBC does not distinguish a gap-limited stop from a proof of
    # optimality in its public status. Require zero gap on that backend.
    cbc = cbc_class(
        msg=False,
        timeLimit=seconds,
        gapRel=0,
        threads=1,
        options=[f"randomSeed {seed}"],
    )
    if cbc.available():
        return cbc
    raise ToolInputError(
        "assign_missions requires HiGHS or CBC; install the backend opt extra"
    )


def build_model(problem):
    import pulp

    model = pulp.LpProblem("assign_missions", pulp.LpMaximize)
    x = {key: variable(model, f"x_{i}") for i, key in enumerate(problem.candidates)}
    y = {mid: variable(model, f"y_{i}") for i, mid in enumerate(problem.missions)}
    model += pulp.lpSum(
        m.value * y[mid] for mid, m in problem.missions.items()
    ) + pulp.lpSum(
        (problem.effect_weight * c.pk - problem.risk_weight * c.risk) * x[key]
        for key, c in problem.candidates.items()
    )
    labels = {}

    def add(expression, label):
        name = f"constraint_{len(labels)}"
        model.addConstraint(expression, name)
        labels[name] = label

    by_mission, by_tail, by_stock, by_base, by_tail_mission = (
        defaultdict(list) for _ in range(5)
    )
    for key, c in problem.candidates.items():
        by_mission[c.assignment.mission_id].append(key)
        by_tail[c.assignment.tail_no].append(key)
        by_tail_mission[c.assignment.tail_no, c.assignment.mission_id].append(key)
        by_base[c.base, c.bucket].append(key)
        for code, count in c.resources.items():
            by_stock[c.base, code].append((key, count))
    for mid, mission in problem.missions.items():
        count = pulp.lpSum(x[key] for key in by_mission[mid])
        add(count >= mission.min_aircraft * y[mid], f"required_aircraft:{mid}")
        add(count <= mission.max_aircraft * y[mid], f"maximum_aircraft:{mid}")
        for dependency in mission.dependencies:
            if dependency not in problem.completed:
                add(
                    y[mid] <= y.get(dependency, 0),
                    f"support_availability:{mid}:{dependency}",
                )
    for (tail, mid), keys in by_tail_mission.items():
        add(pulp.lpSum(x[key] for key in keys) <= 1, f"distinct_aircraft:{tail}:{mid}")
    for tail, keys in by_tail.items():
        for moment in sorted({problem.candidates[k].assignment.depart for k in keys}):
            active = [
                k
                for k in keys
                if problem.candidates[k].assignment.depart
                <= moment
                < problem.candidates[k].ready
            ]
            add(
                pulp.lpSum(x[k] for k in active) <= 1,
                f"aircraft_turnaround:{tail}:{moment.isoformat()}",
            )
        add(
            pulp.lpSum(problem.candidates[k].hours * x[k] for k in keys)
            <= problem.inspection[tail],
            f"inspection_margin:{tail}",
        )
    for (base, code), entries in by_stock.items():
        add(
            pulp.lpSum(count * x[key] for key, count in entries)
            <= problem.stock.get((base, code), 0),
            f"weapon_stock:{base}:{code}",
        )
    for (base, bucket), keys in by_base.items():
        add(
            pulp.lpSum(x[key] for key in keys) <= problem.capacity[base, bucket],
            f"base_capacity:{base}:{bucket}",
        )
    for key in problem.lock_keys:
        add(x[key] == 1, f"locked_assignment:{key}")

    groups = defaultdict(list)
    for key, c in problem.candidates.items():
        for role, (eligible, count) in c.crew.items():
            group = (
                c.assignment.tail_no,
                c.assignment.mission_id,
                c.assignment.depart,
                c.recover,
                role,
                eligible,
                count,
            )
            groups[group].append(key)
    crew_vars, member_groups = {}, defaultdict(list)
    for i, (group, keys) in enumerate(groups.items()):
        tail, mid, depart, _recover, role, eligible, count = group
        crew_vars[group] = {
            member: variable(model, f"crew_{i}_{j}")
            for j, member in enumerate(eligible)
        }
        add(
            pulp.lpSum(crew_vars[group].values())
            == count * pulp.lpSum(x[k] for k in keys),
            f"crew_availability:{tail}:{mid}:{role}:{depart.isoformat()}",
        )
        for member in eligible:
            member_groups[member].append(group)
    for member, slots in member_groups.items():
        # Conservative assignment-layer policy: 12h rest after each sortie.
        for moment in sorted({g[2] for g in slots}):
            active = [g for g in slots if g[2] <= moment < g[3] + timedelta(hours=12)]
            add(
                pulp.lpSum(crew_vars[g][member] for g in active) <= 1,
                f"crew_rest:{member}:{moment.isoformat()}",
            )
            day = [g for g in slots if moment - timedelta(hours=24) < g[2] <= moment]
            add(
                pulp.lpSum(
                    (g[3] - g[2]).total_seconds() / 3600 * crew_vars[g][member]
                    for g in day
                )
                <= 14,
                f"crew_duty:{member}:{moment.isoformat()}",
            )

    if problem.candidates:
        origin = min(c.assignment.depart for c in problem.candidates.values())
        big_m = (
            max((c.ready - origin).total_seconds() for c in problem.candidates.values())
            + 1
        )
        starts = {
            mid: variable(model, f"start_{i}", binary=False, upper=big_m)
            for i, mid in enumerate(problem.missions)
        }
        finishes = {
            mid: variable(model, f"finish_{i}", binary=False, upper=big_m)
            for i, mid in enumerate(problem.missions)
        }
        for key, c in problem.candidates.items():
            mid = c.assignment.mission_id
            finish = c.assignment.tot + timedelta(
                minutes=problem.missions[mid].duration_min
            )
            add(
                finishes[mid] >= (finish - origin).total_seconds() * x[key],
                f"task_finish:{mid}",
            )
            add(
                starts[mid]
                <= (c.assignment.tot - origin).total_seconds() + big_m * (1 - x[key]),
                f"task_start:{mid}",
            )
        for mid, mission in problem.missions.items():
            for dep in mission.dependencies:
                if dep in finishes and dep not in problem.completed:
                    add(
                        finishes[dep] <= starts[mid] + big_m * (1 - y[mid]),
                        f"dependency_window:{mid}:{dep}",
                    )
    for extension in problem.extensions:
        extension(model, x, problem)
    return model, x, y, crew_vars, labels


def solve(problem, options=None) -> Solution:
    if problem.lock_errors:
        return Solution("INFEASIBLE", detail="; ".join(problem.lock_errors))
    if problem.lock_keys:
        from garuda.optimization.assign_missions.validate import validate

        conflicts = validate(problem, sorted(problem.lock_keys), partial=True)
        if conflicts:
            return Solution(
                "INFEASIBLE",
                detail="Locked assignments conflict: " + "; ".join(conflicts),
            )
    if not problem.candidates and not problem.extensions:
        return Solution("OPTIMAL")
    try:
        import pulp
    except ImportError as exc:
        raise ToolInputError(
            "assign_missions requires the backend opt extra (PuLP and HiGHS/CBC)"
        ) from exc
    model, x, _y, crew_vars, labels = build_model(problem)
    seconds = options.time_limit_s if options else problem.time_limit_s
    seed = options.seed if options else problem.seed
    stats = model.solve(solver_factory(seconds, seed))
    status = int(stats.status) if hasattr(stats, "status") else model.status
    if status == -1:
        return Solution(
            "INFEASIBLE",
            detail="Locked assignments or model extensions conflict with hard constraints",
        )
    variables = model.variables()
    valid = all(
        v.value() is not None and isfinite(v.value()) and v.valid(1e-5)
        for v in variables
    )
    valid = valid and model.valid(1e-5)
    if hasattr(stats, "has_solution"):
        valid = valid and stats.has_solution
    if not valid:
        from garuda.optimization.assign_missions.baseline import baseline

        if problem.extensions:
            raise ToolInputError(
                "Solver returned no valid incumbent; increase time_limit_s"
            )
        fallback = baseline(problem)
        if fallback is None:
            raise ToolInputError(
                "Solver returned no valid incumbent satisfying locks; increase time_limit_s"
            )
        fallback.detail = (
            "Solver returned no valid incumbent; using validated greedy assignment"
        )
        return fallback
    optimal = status == 1
    if not hasattr(stats, "has_solution"):
        optimal &= model.sol_status == pulp.LpSolutionOptimal
    if hasattr(model, "solverModel") and hasattr(model.solverModel, "getInfo"):
        gap = model.solverModel.getInfo().mip_gap
        optimal &= isfinite(gap) and gap <= 1e-7
    elif hasattr(stats, "gap_rel") and stats.gap_rel is not None:
        optimal &= stats.gap_rel <= 1e-7
    selected = [key for key, var in x.items() if var.value() > 0.5]
    crew = {
        g: [mid for mid, var in members.items() if var.value() > 0.5]
        for g, members in crew_vars.items()
        if any(var.value() > 0.5 for var in members.values())
    }
    binding = sorted(
        {
            labels[c.name]
            for c in constraints(model)
            if c.name in labels
            and c.sense == -1
            and abs(c.slack or 0) < 1e-5
            and labels[c.name].startswith(
                (
                    "weapon_stock:",
                    "base_capacity:",
                    "aircraft_turnaround:",
                    "inspection_margin:",
                    "crew_rest:",
                    "crew_duty:",
                    "support_availability:",
                )
            )
        }
    )
    return Solution("OPTIMAL" if optimal else "FEASIBLE", selected, crew, binding)
