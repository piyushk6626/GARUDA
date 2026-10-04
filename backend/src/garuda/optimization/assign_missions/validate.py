"""Independent assignment-only validation while the shared validator is a stub."""

import itertools
from collections import Counter, defaultdict
from datetime import timedelta


def crew_witness(candidates):
    """Backtracking feasibility check for the greedy baseline's selected sorties."""
    from itertools import combinations

    demands = []
    for c in candidates:
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
            demands.append(group)
    demands.sort(key=lambda g: (len(g[5]), g[2], g[0], g[4]))
    used, result = defaultdict(list), {}

    def search(i):
        if i == len(demands):
            return True
        g = demands[i]
        available = []
        for member in g[5]:
            slots = [*used[member], g]
            if any(
                g[2] < s[3] + timedelta(hours=12) and s[2] < g[3] + timedelta(hours=12)
                for s in used[member]
            ):
                continue
            if any(
                sum(
                    (s[3] - s[2]).total_seconds() / 3600
                    for s in slots
                    if moment - timedelta(hours=24) < s[2] <= moment
                )
                > 14 + 1e-6
                for moment in (s[2] for s in slots)
            ):
                continue
            available.append(member)
        for members in combinations(available, g[6]):
            for member in members:
                used[member].append(g)
            result[g] = list(members)
            if search(i + 1):
                return True
            result.pop(g)
            for member in members:
                used[member].pop()
        return False

    return result if search(0) else None


def validate(problem, selected, crew=None, *, partial=False):
    errors = []
    if len(set(selected)) != len(selected) or any(
        k not in problem.candidates for k in selected
    ):
        return ["Unknown or duplicate assignment"]
    chosen = [problem.candidates[k] for k in selected]
    missions, aircraft, stocks, capacities = (
        defaultdict(list),
        defaultdict(list),
        Counter(),
        Counter(),
    )
    for c in chosen:
        missions[c.assignment.mission_id].append(c)
        aircraft[c.assignment.tail_no].append(c)
        capacities[c.base, c.bucket] += 1
        stocks.update({(c.base, code): count for code, count in c.resources.items()})
    if not partial and not problem.lock_keys <= set(selected):
        errors.append("Locked assignment missing")
    for mid, candidates in missions.items():
        mission = problem.missions[mid]
        if len(candidates) > mission.max_aircraft or (
            not partial and len(candidates) < mission.min_aircraft
        ):
            errors.append(f"Required aircraft: {mid}")
        if len({c.assignment.tail_no for c in candidates}) != len(candidates):
            errors.append(f"Duplicate aircraft for mission: {mid}")
        if not partial:
            for dep in mission.dependencies:
                if dep in problem.completed:
                    continue
                supports = missions.get(dep, [])
                if not supports or len(supports) < problem.missions[dep].min_aircraft:
                    errors.append(f"Missing dependency: {mid}/{dep}")
                elif max(
                    c.assignment.tot
                    + timedelta(minutes=problem.missions[dep].duration_min)
                    for c in supports
                ) > min(c.assignment.tot for c in candidates):
                    errors.append(f"Dependency finishes too late: {mid}/{dep}")
    for tail, candidates in aircraft.items():
        candidates = sorted(candidates, key=lambda c: c.assignment.depart)
        if any(
            a.ready > b.assignment.depart for a, b in itertools.pairwise(candidates)
        ):
            errors.append(f"Aircraft turnaround: {tail}")
        if sum(c.hours for c in candidates) > problem.inspection[tail] + 1e-6:
            errors.append(f"Inspection margin: {tail}")
    for key, count in stocks.items():
        if count > problem.stock.get(key, 0):
            errors.append(f"Resource stock: {key}")
    for key, count in capacities.items():
        if count > problem.capacity[key]:
            errors.append(f"Base capacity: {key}")
    if errors:
        return errors
    if crew is None:
        crew = crew_witness(chosen)
        if crew is None:
            return ["No feasible crew allocation"]
    expected, used = set(), defaultdict(list)
    for c in chosen:
        for role, (eligible, count) in c.crew.items():
            g = (
                c.assignment.tail_no,
                c.assignment.mission_id,
                c.assignment.depart,
                c.recover,
                role,
                eligible,
                count,
            )
            expected.add(g)
            members = crew.get(g, [])
            if (
                len(set(members)) != count
                or len(members) != count
                or not set(members) <= set(eligible)
            ):
                errors.append(
                    f"Crew eligibility: {c.assignment.tail_no}/{c.assignment.mission_id}/{role}"
                )
            for member in members:
                used[member].append(g)
    if set(crew) != expected:
        errors.append("Unexpected crew allocation")
    for member, slots in used.items():
        slots.sort(key=lambda g: g[2])
        if any(a[3] + timedelta(hours=12) > b[2] for a, b in itertools.pairwise(slots)):
            errors.append(f"Crew rest: {member}")
        if any(
            sum(
                (g[3] - g[2]).total_seconds() / 3600
                for g in slots
                if moment - timedelta(hours=24) < g[2] <= moment
            )
            > 14 + 1e-6
            for moment in (g[2] for g in slots)
        ):
            errors.append(f"Crew duty: {member}")
    return errors
