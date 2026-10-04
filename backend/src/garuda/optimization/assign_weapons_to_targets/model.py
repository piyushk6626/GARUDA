"""MILP weapon-target assignment.

Survival of target t is ``exp(u_t)`` with ``u_t = sum x * ln(1 - Pk)``.
That exponential is replaced by a piecewise-linear interpolation on a uniform
grid in u (adjacent-lambda / SOS2 style), and the model minimises expected
surviving value. Coverage thresholds are hard constraints; if they make the
model infeasible the solve is repeated without them and the shortfall shows
up in ``kill_prob_by_target``.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from garuda.optimization.assign_weapons_to_targets.baseline import baseline
from garuda.optimization.assign_weapons_to_targets.problem import (
    AssignmentKey,
    WTAProblem,
    WTASolution,
    freeze_counts,
)

PWL_STEPS = 12


@dataclass(frozen=True)
class SolveOptions:
    time_limit_s: float = 20.0
    seed: int = 42


def solve(problem: WTAProblem, options: SolveOptions | None = None) -> WTASolution:
    options = options or SolveOptions()
    if not problem.pk or not any(qty > 0 for qty in problem.stock.values()):
        return freeze_counts({})
    try:
        import pulp
    except ImportError as exc:
        raise RuntimeError("method 'milp' needs PuLP; install the opt extra.") from exc
    counts = _solve_milp(pulp, problem, options, enforce_coverage=True)
    if counts is None and problem.min_destroy_prob:
        counts = _solve_milp(pulp, problem, options, enforce_coverage=False)
    if counts is None:
        return baseline(problem)
    return freeze_counts(counts)


def _solve_milp(pulp, problem: WTAProblem, options: SolveOptions, enforce_coverage: bool):
    keys = [
        key
        for key, pk in problem.pk.items()
        if pk > 0 and problem.stock.get((key[0], key[1]), 0) > 0
    ]
    if not keys:
        return {}
    prob = pulp.LpProblem("wta", pulp.LpMinimize)
    assign = {
        key: prob.add_variable(f"x_{index}", 0, problem.x_max, cat="Integer")
        for index, key in enumerate(keys)
    }
    _add_stock_limits(pulp, prob, problem, assign)
    _add_shot_limits(pulp, prob, problem, assign)
    _add_platform_limits(pulp, prob, problem, assign)
    survival = {
        target_id: _survival_expr(pulp, prob, problem, assign, target_id)
        for target_id in problem.target_ids
    }
    if enforce_coverage:
        for target_id, theta in problem.min_destroy_prob.items():
            prob += survival[target_id] <= 1.0 - theta
    prob += pulp.lpSum(problem.values[target_id] * survival[target_id] for target_id in problem.target_ids)
    stats = prob.solve(pulp.HiGHS(msg=False, timeLimit=options.time_limit_s, gapRel=1e-4, threads=1))
    if stats.status != pulp.LpSolveStatus.Optimal:
        return None
    return _read_counts(pulp, assign)


def _add_stock_limits(pulp, prob, problem: WTAProblem, assign: dict[AssignmentKey, object]) -> None:
    groups: dict[tuple[str, str], list[AssignmentKey]] = defaultdict(list)
    for key in assign:
        groups[(key[0], key[1])].append(key)
    for (base_id, weapon_code), group in groups.items():
        prob += pulp.lpSum(assign[key] for key in group) <= problem.stock[(base_id, weapon_code)]


def _add_shot_limits(pulp, prob, problem: WTAProblem, assign: dict[AssignmentKey, object]) -> None:
    groups: dict[tuple[str, str], list[AssignmentKey]] = defaultdict(list)
    for key in assign:
        groups[(key[1], key[2])].append(key)
    for group in groups.values():
        prob += pulp.lpSum(assign[key] for key in group) <= problem.x_max


def _add_platform_limits(pulp, prob, problem: WTAProblem, assign: dict[AssignmentKey, object]) -> None:
    if problem.platform_capacity is None:
        return
    groups: dict[str, list[AssignmentKey]] = defaultdict(list)
    for key in assign:
        groups[key[0]].append(key)
    for base_id, group in groups.items():
        cap = problem.platform_capacity.get(base_id)
        if cap is not None:
            prob += pulp.lpSum(assign[key] for key in group) <= cap


def _survival_expr(pulp, prob, problem: WTAProblem, assign, target_id: str):
    terms = []
    for (base_id, weapon_code, candidate), pk in problem.pk.items():
        key = (base_id, weapon_code, candidate)
        if candidate != target_id or key not in assign or pk <= 0:
            continue
        terms.append(assign[key] * _log_miss(pk))
    if not terms:
        return 1.0
    return _piecewise_exp(pulp, prob, target_id, pulp.lpSum(terms), _u_floor(problem, target_id))


def _piecewise_exp(pulp, prob, name: str, u_expr, u_floor: float):
    """Interpolate exp(u) with adjacent lambdas on a uniform grid in u."""
    if u_floor >= -1e-9:
        return 1.0
    grid_u = [u_floor * step / PWL_STEPS for step in range(PWL_STEPS + 1)]
    grid_s = [math.exp(u) for u in grid_u]
    safe = _safe_name(name)
    lambdas = [prob.add_variable(f"lam_{safe}_{index}", 0, 1) for index in range(len(grid_u))]
    segments = [prob.add_variable(f"seg_{safe}_{index}", cat="Binary") for index in range(PWL_STEPS)]
    prob += pulp.lpSum(lambdas) == 1, f"lam_sum_{safe}"
    prob += pulp.lpSum(segments) == 1, f"seg_sum_{safe}"
    for index, lam in enumerate(lambdas):
        adjacent = []
        if index < len(segments):
            adjacent.append(segments[index])
        if index > 0:
            adjacent.append(segments[index - 1])
        prob += lam <= pulp.lpSum(adjacent), f"touch_{safe}_{index}"
    prob += u_expr == pulp.lpSum(lam * u for lam, u in zip(lambdas, grid_u)), f"u_{safe}"
    return pulp.lpSum(lam * survival for lam, survival in zip(lambdas, grid_s))


def _u_floor(problem: WTAProblem, target_id: str) -> float:
    best_log: dict[str, float] = {}
    for (_base_id, weapon_code, candidate), pk in problem.pk.items():
        if candidate != target_id or pk <= 0:
            continue
        log_miss = _log_miss(pk)
        best_log[weapon_code] = min(best_log.get(weapon_code, 0.0), log_miss)
    if not best_log:
        return 0.0
    return problem.x_max * sum(best_log.values())


def _log_miss(pk: float) -> float:
    return math.log1p(-min(pk, 1.0 - 1e-9))


def _read_counts(pulp, assign: dict[AssignmentKey, object]) -> dict[AssignmentKey, int] | None:
    counts: dict[AssignmentKey, int] = {}
    for key, var in assign.items():
        raw = pulp.value(var)
        if raw is None:
            return None
        count = int(round(raw))
        if count > 0:
            counts[key] = count
    return counts


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)
