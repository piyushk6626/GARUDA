"""Bounded, warm-started MILP with a restricted-neighbourhood first pass."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from math import isfinite
from time import perf_counter

from garuda.common import as_utc
from garuda.contracts.plan import Assignment
from garuda.contracts.tool import ToolInputError
from garuda.optimization.retask_rolling_horizon.problem import assignment_key


@dataclass
class Solution:
    status: str
    assignments: list[Assignment] = field(default_factory=list)
    selected: list = field(default_factory=list)
    degraded: set[str] = field(default_factory=set)
    objective: float = float("-inf")
    widened: bool = False


def _variable(lp, pulp, name, cat="Binary"):
    # PuLP 4 moved variable ownership into LpProblem; 2/3 use constructors.
    if hasattr(lp, "add_variable"):
        return lp.add_variable(name, lowBound=0, upBound=1, cat=cat)
    return pulp.LpVariable(name, lowBound=0, upBound=1, cat=cat)


def _solver(pulp, seconds, seed):
    if hasattr(pulp, "HiGHS") and pulp.HiGHS().available():

        class WarmHiGHS(pulp.HiGHS):
            def buildSolverModel(self, lp):
                super().buildSolverModel(lp)
                # Explicit injection also supports PuLP versions predating the
                # HiGHS warmStart option. Only assignment/fulfilment hints are set.
                variables = (
                    lp.exported_variables()
                    if hasattr(lp, "exported_variables")
                    else lp.variables()
                )
                hints = [
                    (getattr(v, "index", i), v.varValue)
                    for i, v in enumerate(variables)
                    if v.varValue is not None
                ]
                if hints:
                    indices, values = zip(*hints)
                    lp.solverModel.setSolution(len(hints), list(indices), list(values))

        return WarmHiGHS(msg=False, timeLimit=seconds, threads=1, random_seed=seed)
    for name in ("PULP_CBC_CMD", "COIN_CMD"):
        factory = getattr(pulp, name, None)
        if factory is not None and factory(msg=False).available():
            return factory(
                msg=False,
                timeLimit=seconds,
                warmStart=True,
                threads=1,
                options=[f"randomSeed {seed}"],
            )
    raise ToolInputError(
        "Retasking requires an available HiGHS or CBC solver; install garuda[opt]"
    )


def neighbourhood(problem):
    """Release affected missions, resource competitors, and dependencies."""
    free = set(problem.affected)
    affected_candidates = [
        c for c in problem.candidates if c.assignment.mission_id in free
    ]
    for candidate in problem.candidates:
        for impacted in affected_candidates:
            if (
                as_utc(candidate.assignment.depart) >= impacted.end
                or as_utc(impacted.assignment.depart) >= candidate.end
            ):
                continue
            shared_tail = candidate.assignment.tail_no == impacted.assignment.tail_no
            shared_base = candidate.base_id == impacted.base_id
            if shared_tail or shared_base:
                free.add(candidate.assignment.mission_id)
                break
    while True:
        linked = set(free)
        for mid, mission in problem.missions.items():
            if mid in free or free.intersection(mission.dependencies):
                linked.add(mid)
                linked.update(mission.dependencies)
        if linked == free:
            break
        free = linked
    return free - problem.frozen_missions


def _frozen_extension(lp, variables, problem):
    old = {assignment_key(a) for a in problem.locked}
    for candidate in problem.candidates:
        if candidate.assignment.mission_id in problem.frozen_missions:
            lp += variables[candidate.key] == int(
                assignment_key(candidate.assignment) in old
            )


def _stability_extension(lp, variables, problem):
    import pulp

    old = {assignment_key(a) for a in problem.plan.assignments}
    penalty = []
    represented = set()
    for index, candidate in enumerate(problem.candidates):
        key = assignment_key(candidate.assignment)
        represented.add(key)
        previous = int(key in old)
        change = _variable(lp, pulp, f"change_{index}")
        x = variables[candidate.key]
        lp += change >= previous - x
        lp += change >= x - previous
        penalty.append(change)
    lost = len(old - represented - {assignment_key(a) for a in problem.historical})
    lp.setObjective(
        lp.objective - problem.stability_weight * (pulp.lpSum(penalty) + lost)
    )


def _attempt(problem, free, seconds):
    import pulp

    deadline = perf_counter() + seconds
    lp = pulp.LpProblem("rolling_horizon_retask", pulp.LpMaximize)
    candidates = problem.candidates
    variables = {c.key: _variable(lp, pulp, f"x_{i}") for i, c in enumerate(candidates)}
    by_mission, by_tail, by_base, by_stock = (defaultdict(list) for _ in range(4))
    old = {assignment_key(a) for a in problem.plan.assignments}
    for i, candidate in enumerate(candidates):
        a = candidate.assignment
        by_mission[a.mission_id].append(i)
        by_tail[a.tail_no].append(i)
        by_base[
            candidate.base_id,
            as_utc(a.depart).replace(minute=0, second=0, microsecond=0),
        ].append(i)
        for weapon in candidate.weapons:
            by_stock[candidate.base_id, weapon].append(i)
        x = variables[candidate.key]
        x.setInitialValue(int(assignment_key(a) in old and candidate.valid))
        if not candidate.valid:
            lp += x == 0
        if (
            free is not None
            and a.mission_id not in free
            and a.mission_id not in problem.frozen_missions
        ):
            lp += x == int(assignment_key(a) in old)
    active = set(by_mission) | set(problem.affected) | set(problem.reasons)
    y = {
        mid: _variable(lp, pulp, f"fulfilled_{i}")
        for i, mid in enumerate(sorted(active))
    }
    historical = {a.mission_id for a in problem.historical}
    previously_assigned = {a.mission_id for a in problem.plan.assignments} - historical
    for mid in sorted(active):
        mission = problem.missions[mid]
        indices = by_mission[mid]
        total = pulp.lpSum(variables[candidates[i].key] for i in indices)
        lp += total >= mission.min_aircraft * y[mid]
        lp += total <= mission.max_aircraft * y[mid]
        y[mid].setInitialValue(
            int(
                sum(
                    assignment_key(candidates[i].assignment) in old
                    and candidates[i].valid
                    for i in indices
                )
                >= mission.min_aircraft
            )
        )
        # A package has a single TOT, even when several tails are selected.
        groups = defaultdict(list)
        for i in indices:
            groups[as_utc(candidates[i].assignment.tot)].append(i)
        choices = []
        for j, group in enumerate(groups.values()):
            group_var = _variable(lp, pulp, f"package_{mid}_{j}")
            lp += (
                pulp.lpSum(variables[candidates[i].key] for i in group)
                <= mission.max_aircraft * group_var
            )
            choices.append(group_var)
        lp += pulp.lpSum(choices) == y[mid]
        for dep in mission.dependencies:
            lp += y[mid] <= (1 if dep in historical else y.get(dep, 0))
            for i in indices:
                for j in by_mission.get(dep, []):
                    if as_utc(candidates[j].assignment.tot) > as_utc(
                        candidates[i].assignment.tot
                    ):
                        lp += (
                            variables[candidates[i].key] + variables[candidates[j].key]
                            <= 1
                        )
    lp += pulp.lpSum(problem.missions[mid].value * var for mid, var in y.items())
    lp.setObjective(
        lp.objective
        + pulp.lpSum((0.3 * c.pk - 0.2 * c.risk) * variables[c.key] for c in candidates)
        - pulp.lpSum(
            0.1 * problem.missions[mid].value * (1 - y[mid])
            for mid in previously_assigned
            if mid in y
        )
    )
    for tail, indices in by_tail.items():
        # Interval cliques enforce occupancy through recovery and turnaround.
        for at in sorted({as_utc(candidates[i].assignment.depart) for i in indices}):
            overlap = [
                i
                for i in indices
                if as_utc(candidates[i].assignment.depart) <= at < candidates[i].end
            ]
            lp += pulp.lpSum(variables[candidates[i].key] for i in overlap) <= 1
        lp += (
            pulp.lpSum(
                candidates[i].flight_hours * variables[candidates[i].key]
                for i in indices
            )
            <= problem.inspection_hours[tail]
        )
    for key, indices in by_base.items():
        lp += (
            pulp.lpSum(variables[candidates[i].key] for i in indices)
            <= problem.capacities[key]
        )
    for key, indices in by_stock.items():
        lp += pulp.lpSum(
            candidates[i].weapons[key[1]] * variables[candidates[i].key]
            for i in indices
        ) <= problem.stocks.get(key, 0)

    # Assign anonymous eligible crew to candidates solely to prove feasibility.
    # The dedicated crew tool remains responsible for materialising schedules.
    crew_use = defaultdict(list)
    for i, candidate in enumerate(candidates):
        for role, count in candidate.staffing.items():
            choices = []
            for j, crew_id in enumerate(candidate.crew.get(role, ())):
                z = _variable(lp, pulp, f"crew_{i}_{role}_{j}")
                if crew_id in candidate.fixed_crew.get(role, ()):
                    lp += z == variables[candidate.key]
                choices.append(z)
                crew_use[crew_id].append((i, z))
            lp += pulp.lpSum(choices) == count * variables[candidate.key]
    for crew_id, choices in crew_use.items():
        for at in sorted({as_utc(candidates[i].assignment.depart) for i, _ in choices}):
            lp += (
                pulp.lpSum(
                    z
                    for i, z in choices
                    if as_utc(candidates[i].assignment.depart) <= at < candidates[i].end
                )
                <= 1
            )
            lp += (
                pulp.lpSum(
                    candidates[i].flight_hours * z
                    for i, z in choices
                    if at
                    <= as_utc(candidates[i].assignment.depart)
                    < at + timedelta(hours=24)
                )
                <= 14 - problem.crew_duty[crew_id]
            )

    # Couple receiver selection to surviving, overlapping AAR sorties and fuel.
    tankers = [
        i
        for i, c in enumerate(candidates)
        if problem.missions[c.assignment.mission_id].type == "AAR"
    ]
    fuel_use = defaultdict(list)
    for i, receiver in enumerate(candidates):
        if not receiver.needs_aar:
            continue
        choices = []
        for j in tankers:
            tanker = candidates[j]
            if tanker.assignment.tail_no == receiver.assignment.tail_no:
                continue
            tanker_end = as_utc(tanker.assignment.tot) + timedelta(
                minutes=problem.missions[tanker.assignment.mission_id].duration_min
            )
            if not (
                as_utc(tanker.assignment.tot)
                <= as_utc(receiver.assignment.tot)
                <= tanker_end
            ):
                continue
            z = _variable(lp, pulp, f"aar_{i}_{j}")
            lp += z <= variables[tanker.key]
            choices.append(z)
            fuel_use[j].append(receiver.fuel_demand * z)
        lp += pulp.lpSum(choices) == variables[receiver.key]
    for j, demand in fuel_use.items():
        lp += (
            pulp.lpSum(demand)
            <= candidates[j].tanker_fuel * variables[candidates[j].key]
        )

    for extension in (_frozen_extension, _stability_extension, *problem.extensions):
        extension(lp, variables, problem)
    remaining = deadline - perf_counter()
    if remaining <= 0:
        return Solution("ERROR")
    result = lp.solve(_solver(pulp, remaining, problem.seed))
    # PuLP 4 has explicit incumbent metadata; PuLP 2/3 expose sol_status.
    has_solution = getattr(result, "has_solution", None)
    if has_solution is None:
        has_solution = getattr(lp, "sol_status", 0) in (1, 2)
    constraints = (
        lp.constraints() if callable(lp.constraints) else lp.constraints.values()
    )
    all_variables = [v for v in lp.variables() if v.name != "__dummy"]
    # Never extract seeded values, fractional relaxations, or invalid incumbents.
    feasible = bool(has_solution) and all(
        v.varValue is not None
        and isfinite(v.varValue)
        and abs(v.varValue - round(v.varValue)) < 1e-5
        for v in all_variables
    )
    feasible = feasible and all(c.valid(1e-5) for c in constraints)
    if not feasible:
        return Solution(
            "INFEASIBLE"
            if str(getattr(result, "status", getattr(lp, "status", None))).lower()
            in {"-1", "infeasible", "lpsolvestatus.infeasible"}
            else "ERROR"
        )
    selected = [c for c in candidates if variables[c.key].value() > 0.5]
    fulfilled = {mid for mid, var in y.items() if var.value() > 0.5}
    degraded = (previously_assigned | problem.affected) - fulfilled - historical
    degraded |= {c.assignment.mission_id for c in selected if c.route_replan}
    status_value = getattr(result, "status", getattr(lp, "status", None))
    optimal = str(status_value).lower() in {"1", "optimal", "lpsolvestatus.optimal"}
    if not hasattr(result, "has_solution"):
        optimal = optimal and getattr(lp, "sol_status", 0) == 1
    # A restricted optimum is not a proof of optimality for the full problem.
    status = (
        "OPTIMAL"
        if optimal and free is None
        else "FEASIBLE"
        if optimal
        else "FEASIBLE_NOT_OPTIMAL"
    )
    return Solution(
        status,
        problem.historical + [c.assignment for c in selected],
        selected,
        degraded,
        float(pulp.value(lp.objective)),
    )


def solve(problem, options=None):
    try:
        import pulp  # noqa: F401
    except ImportError as exc:
        raise ToolInputError(
            "Retasking requires garuda[opt] (PuLP and HiGHS/CBC)"
        ) from exc
    seconds = options.time_limit_s if options is not None else problem.time_limit_s
    deadline = perf_counter() + seconds
    if problem.frozen_conflicts:
        return Solution("INFEASIBLE", degraded=set(problem.frozen_conflicts))
    free = neighbourhood(problem) if problem.mode == "fast" else None
    first = _attempt(problem, free, seconds * 0.5 if free is not None else seconds)
    if free is None:
        if first.status != "ERROR" or problem.extensions:
            return first
        from garuda.optimization.retask_rolling_horizon.baseline import baseline

        return baseline(problem)
    lost = first.degraded - problem.route_replan
    # Failed repair or a sacrificed mission widens to a full solve, sharing one
    # caller budget. Full mode retains all frozen constraints.
    if first.status in {"INFEASIBLE", "ERROR"} or lost:
        remaining = deadline - perf_counter()
        if remaining > 0.01:
            full = _attempt(problem, None, remaining)
            full.widened = True
            if (
                full.status not in {"INFEASIBLE", "ERROR"}
                and full.objective >= first.objective
            ):
                return full
    if first.status == "ERROR" and not problem.extensions:
        from garuda.optimization.retask_rolling_horizon.baseline import baseline

        return baseline(problem)
    return first
