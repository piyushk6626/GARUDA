"""Greedy marginal-return assignment, then pairwise local improvement.

Each added weapon multiplies that target's survival by (1 - Pk), so the
value of the next shot on the same target falls. The greedy step picks the
feasible (base, weapon, target) with the largest
``value * survival * pk / cost``, and a coverage shortfall outranks that
gain until ``min_destroy_prob`` is met or stock runs out.
"""

from collections import defaultdict

from garuda.optimization.assign_weapons_to_targets.problem import (
    SHORTFALL_WEIGHT,
    AssignmentKey,
    WTAProblem,
    WTASolution,
    freeze_counts,
    penalized_value,
    survival_by_target,
)


def baseline(problem: WTAProblem) -> WTASolution:
    counts: dict[AssignmentKey, int] = {}
    while _assign_one(problem, counts):
        pass
    return freeze_counts(local_search(problem, counts))


def local_search(problem: WTAProblem, counts: dict[AssignmentKey, int]) -> dict[AssignmentKey, int]:
    """Swap or move single weapons while the penalized value improves."""
    counts = {key: count for key, count in counts.items() if count > 0}
    for _ in range(24):
        survival = survival_by_target(problem, counts)
        current = penalized_value(problem, survival)
        move = _best_move(problem, counts, survival, current)
        if move is None:
            break
        _apply(counts, move[0], move[1])
    return counts


def _assign_one(problem: WTAProblem, counts: dict[AssignmentKey, int]) -> bool:
    survival = survival_by_target(problem, counts)
    stock_left = _stock_left(problem, counts)
    launched = _launched(counts)
    shots = _shots(counts)
    best_key: AssignmentKey | None = None
    best_gain = problem.eps
    for key in sorted(problem.pk):
        base_id, weapon_code, target_id = key
        if stock_left.get((base_id, weapon_code), 0) <= 0:
            continue
        if shots.get((weapon_code, target_id), 0) >= problem.x_max:
            continue
        if not _has_station(problem, base_id, launched):
            continue
        gain = _marginal_gain(problem, survival, target_id, problem.pk[key], weapon_code)
        if gain > best_gain:
            best_gain = gain
            best_key = key
    if best_key is None:
        return False
    counts[best_key] = counts.get(best_key, 0) + 1
    return True


def _marginal_gain(problem: WTAProblem, survival: dict[str, float], target_id: str, pk: float, weapon_code: str) -> float:
    if pk <= 0:
        return 0.0
    value_gain = problem.values[target_id] * survival[target_id] * pk / problem.cost.get(weapon_code, 1.0)
    theta = problem.min_destroy_prob.get(target_id, 0.0)
    if theta <= 0:
        return value_gain
    kill = 1.0 - survival[target_id]
    shortfall = theta - kill
    if shortfall <= 0:
        return value_gain
    covered = min(shortfall, survival[target_id] * pk)
    return value_gain + SHORTFALL_WEIGHT * covered


def _best_move(problem, counts, survival, current: float):
    stock_left = _stock_left(problem, counts)
    launched = _launched(counts)
    best = None
    best_value = current
    assigned = [key for key, count in counts.items() if count > 0]
    for source in assigned:
        for dest in problem.pk:
            if not _move_ok(problem, counts, stock_left, launched, source, dest):
                continue
            trial = _survival_after(problem, survival, source, dest)
            value = penalized_value(problem, trial)
            if value > best_value + 1e-6:
                best_value = value
                best = (source, dest)
    return best


def _move_ok(problem, counts, stock_left, launched, source: AssignmentKey, dest: AssignmentKey) -> bool:
    if source == dest or counts.get(source, 0) <= 0 or dest not in problem.pk:
        return False
    src_base, src_weapon, src_target = source
    dst_base, dst_weapon, dst_target = dest
    used = sum(
        count
        for (_base, weapon_code, target_id), count in counts.items()
        if weapon_code == dst_weapon and target_id == dst_target
    )
    if (src_weapon, src_target) == (dst_weapon, dst_target):
        used -= 1
    if used + 1 > problem.x_max:
        return False
    if (src_base, src_weapon) != (dst_base, dst_weapon) and stock_left.get((dst_base, dst_weapon), 0) <= 0:
        return False
    if src_base != dst_base and not _has_station(problem, dst_base, launched):
        return False
    return True


def _survival_after(problem, survival, source: AssignmentKey, dest: AssignmentKey) -> dict[str, float]:
    updated = dict(survival)
    src_target = source[2]
    dst_target = dest[2]
    src_pk = problem.pk[source]
    dst_pk = problem.pk[dest]
    if src_target == dst_target:
        updated[src_target] = survival[src_target] / (1.0 - src_pk) * (1.0 - dst_pk)
        return updated
    updated[src_target] = survival[src_target] / (1.0 - src_pk)
    updated[dst_target] = survival[dst_target] * (1.0 - dst_pk)
    return updated


def _apply(counts: dict[AssignmentKey, int], source: AssignmentKey, dest: AssignmentKey) -> None:
    counts[source] -= 1
    if counts[source] <= 0:
        del counts[source]
    counts[dest] = counts.get(dest, 0) + 1


def _stock_left(problem: WTAProblem, counts: dict[AssignmentKey, int]) -> dict[tuple[str, str], int]:
    used: dict[tuple[str, str], int] = defaultdict(int)
    for (base_id, weapon_code, _target_id), count in counts.items():
        used[(base_id, weapon_code)] += count
    return {key: qty - used[key] for key, qty in problem.stock.items()}


def _launched(counts: dict[AssignmentKey, int]) -> dict[str, int]:
    launched: dict[str, int] = defaultdict(int)
    for (base_id, _weapon_code, _target_id), count in counts.items():
        launched[base_id] += count
    return launched


def _shots(counts: dict[AssignmentKey, int]) -> dict[tuple[str, str], int]:
    shots: dict[tuple[str, str], int] = defaultdict(int)
    for (_base_id, weapon_code, target_id), count in counts.items():
        shots[(weapon_code, target_id)] += count
    return shots


def _has_station(problem: WTAProblem, base_id: str, launched: dict[str, int]) -> bool:
    if problem.platform_capacity is None:
        return True
    cap = problem.platform_capacity.get(base_id)
    if cap is None:
        return True
    return launched.get(base_id, 0) < cap
