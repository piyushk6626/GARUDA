"""Solver-independent feasibility checks and a conservative greedy fallback."""

from collections import Counter, defaultdict
from datetime import timedelta
from itertools import combinations, islice, pairwise
from time import perf_counter

from garuda.common import as_utc
from garuda.optimization.retask_rolling_horizon.problem import assignment_key


def validate_selection(problem, selected, *, complete=True, deadline=None):
    """Check concrete selections without LP variables or solver constraints.

    Crew slots use a bounded backtracking assignment: exhaustion conservatively
    rejects the selection rather than accepting an unproved crew schedule.
    """
    errors = []
    by_mission, by_tail = defaultdict(list), defaultdict(list)
    stock, base_count = Counter(), Counter()
    for c in selected:
        a = c.assignment
        if not c.valid:
            errors.append(f"invalid:{a.mission_id}:{c.reason}")
        by_mission[a.mission_id].append(c)
        by_tail[a.tail_no].append(c)
        base_count[
            c.base_id, as_utc(a.depart).replace(minute=0, second=0, microsecond=0)
        ] += 1
        stock.update({(c.base_id, code): count for code, count in c.weapons.items()})
    for key, count in stock.items():
        if count > problem.stocks.get(key, 0):
            errors.append(f"stock:{key}")
    for key, count in base_count.items():
        if count > problem.capacities[key]:
            errors.append(f"capacity:{key}")
    for tail, sorties in by_tail.items():
        ordered = sorted(sorties, key=lambda c: as_utc(c.assignment.depart))
        if any(a.end > as_utc(b.assignment.depart) for a, b in pairwise(ordered)):
            errors.append(f"overlap:{tail}")
        if sum(c.flight_hours for c in sorties) > problem.inspection_hours[tail] + 1e-6:
            errors.append(f"inspection:{tail}")
    historical = {a.mission_id for a in problem.historical}
    for mid, package in by_mission.items():
        mission = problem.missions[mid]
        if not mission.min_aircraft <= len(package) <= mission.max_aircraft:
            errors.append(f"package:{mid}")
        if len({as_utc(c.assignment.tot) for c in package}) != 1:
            errors.append(f"tot:{mid}")
        if complete:
            for dep in mission.dependencies:
                if dep not in historical and dep not in by_mission:
                    errors.append(f"dependency:{mid}:{dep}")
                elif dep in by_mission and min(
                    as_utc(c.assignment.tot) for c in package
                ) < max(as_utc(c.assignment.tot) for c in by_mission[dep]):
                    errors.append(f"dependency_time:{mid}:{dep}")
    if complete:
        keys = {assignment_key(c.assignment) for c in selected}
        if not {assignment_key(a) for a in problem.locked} <= keys:
            errors.append("frozen")
    if errors:
        return errors

    # Each staffing position is a slot. Pick the most constrained slot first.
    jobs = []
    for c in selected:
        for role, count in c.staffing.items():
            fixed = c.fixed_crew.get(role, ())
            jobs.extend((c, (cid,)) for cid in fixed)
            jobs.extend(
                (c, tuple(cid for cid in c.crew.get(role, ()) if cid not in fixed))
                for _ in range(count - len(fixed))
            )
    jobs.sort(key=lambda item: len(item[1]))
    crew_use = defaultdict(list)
    steps = 0

    def assign(index):
        nonlocal steps
        steps += 1
        if steps > 10000 or (deadline is not None and perf_counter() >= deadline):
            return False
        if index == len(jobs):
            return True
        candidate, eligible = jobs[index]
        start = as_utc(candidate.assignment.depart)
        for cid in eligible:
            used = crew_use[cid]
            if any(
                start < c.end and as_utc(c.assignment.depart) < candidate.end
                for c in used
            ):
                continue
            proposed = used + [candidate]
            if any(
                sum(
                    c.flight_hours
                    for c in proposed
                    if at <= as_utc(c.assignment.depart) < at + timedelta(hours=24)
                )
                > 14 - problem.crew_duty[cid] + 1e-6
                for at in {as_utc(c.assignment.depart) for c in proposed}
            ):
                continue
            used.append(candidate)
            if assign(index + 1):
                return True
            used.pop()
        return False

    if not assign(0):
        return ["crew"]
    receivers = [c for c in selected if c.needs_aar]
    tankers = [
        c for c in selected if problem.missions[c.assignment.mission_id].type == "AAR"
    ]
    fuel_used = Counter()

    def refuel(index):
        if deadline is not None and perf_counter() >= deadline:
            return False
        if index == len(receivers):
            return True
        receiver = receivers[index]
        for j, tanker in enumerate(tankers):
            end = as_utc(tanker.assignment.tot) + timedelta(
                minutes=problem.missions[tanker.assignment.mission_id].duration_min
            )
            if (
                tanker.assignment.tail_no == receiver.assignment.tail_no
                or not as_utc(tanker.assignment.tot)
                <= as_utc(receiver.assignment.tot)
                <= end
            ):
                continue
            if fuel_used[j] + receiver.fuel_demand > tanker.tanker_fuel + 1e-6:
                continue
            fuel_used[j] += receiver.fuel_demand
            if refuel(index + 1):
                return True
            fuel_used[j] -= receiver.fuel_demand
        return False

    if not refuel(0):
        errors.append("tanker")
    return errors


def objective(problem, selected):
    fulfilled = {c.assignment.mission_id for c in selected}
    historical = {assignment_key(a) for a in problem.historical}
    old = {assignment_key(a) for a in problem.plan.assignments} - historical
    new = {assignment_key(c.assignment) for c in selected}
    preempted = {
        a.mission_id
        for a in problem.plan.assignments
        if assignment_key(a) not in historical
    } - fulfilled
    return (
        sum(problem.missions[mid].value for mid in fulfilled)
        + sum(0.3 * c.pk - 0.2 * c.risk for c in selected)
        - problem.stability_weight * len(old ^ new)
        - sum(0.1 * problem.missions[mid].value for mid in preempted)
    )


def baseline(problem):
    from garuda.optimization.retask_rolling_horizon.model import Solution

    locked = {assignment_key(a) for a in problem.locked}
    deadline = perf_counter() + min(1.0, problem.time_limit_s)
    selected = [c for c in problem.candidates if assignment_key(c.assignment) in locked]
    if problem.frozen_conflicts or validate_selection(
        problem, selected, complete=False, deadline=deadline
    ):
        return Solution(
            "INFEASIBLE",
            degraded=set(problem.frozen_conflicts)
            or {a.mission_id for a in problem.locked},
        )
    old = {assignment_key(a) for a in problem.plan.assignments}
    remaining = {
        c.assignment.mission_id for c in problem.candidates
    } - problem.frozen_missions
    historical = {a.mission_id for a in problem.historical}
    while remaining:
        if perf_counter() >= deadline:
            break
        fulfilled = {c.assignment.mission_id for c in selected} | historical
        ready = [
            mid
            for mid in remaining
            if set(problem.missions[mid].dependencies) <= fulfilled
        ]
        if not ready:
            break
        mid = min(
            ready,
            key=lambda mid: (
                problem.missions[mid].type != "AAR",
                problem.missions[mid].priority,
                -problem.missions[mid].value,
                mid,
            ),
        )
        remaining.remove(mid)
        groups = defaultdict(list)
        for c in problem.candidates:
            if c.valid and c.assignment.mission_id == mid:
                groups[as_utc(c.assignment.tot)].append(c)
        packages = []
        for group in groups.values():
            group.sort(
                key=lambda c: (
                    assignment_key(c.assignment) not in old,
                    c.risk,
                    c.assignment.tail_no,
                )
            )
            for package in islice(
                combinations(group, problem.missions[mid].min_aircraft), 256
            ):
                if perf_counter() >= deadline:
                    break
                if not validate_selection(
                    problem, selected + list(package), complete=False, deadline=deadline
                ):
                    packages.append(package)
        if packages:
            selected.extend(
                max(
                    packages,
                    key=lambda package: objective(problem, selected + list(package)),
                )
            )
    if validate_selection(problem, selected, deadline=deadline):
        return Solution("INFEASIBLE", degraded=set(problem.affected))
    fulfilled = {c.assignment.mission_id for c in selected} | historical
    degraded = (
        {a.mission_id for a in problem.plan.assignments} | problem.affected
    ) - fulfilled
    degraded |= {c.assignment.mission_id for c in selected if c.route_replan}
    return Solution(
        "FEASIBLE_NOT_OPTIMAL",
        problem.historical + [c.assignment for c in selected],
        selected,
        degraded,
        objective(problem, selected),
    )
