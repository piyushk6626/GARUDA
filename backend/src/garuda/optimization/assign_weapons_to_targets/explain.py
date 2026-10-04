"""Turn an assignment into the tool output: value, kill probability, stock left."""

from collections import defaultdict

from garuda.optimization.assign_weapons_to_targets.problem import (
    WTAProblem,
    WTASolution,
    counts_map,
    destroyed_value,
    survival_by_target,
)
from garuda.optimization.assign_weapons_to_targets.schemas import (
    AssignWeaponsToTargetsOutput,
    WeaponAllocation,
)


def explain(problem: WTAProblem, solution: WTASolution) -> AssignWeaponsToTargetsOutput:
    counts = counts_map(solution)
    survival = survival_by_target(problem, counts)
    allocations = [
        WeaponAllocation(
            target_id=target_id,
            weapon_code=weapon_code,
            count=count,
            base_id=base_id,
        )
        for base_id, weapon_code, target_id, count in solution.counts
    ]
    return AssignWeaponsToTargetsOutput(
        allocations=allocations,
        expected_destroyed_value=destroyed_value(problem, survival),
        kill_prob_by_target={
            target_id: 1.0 - survival[target_id] for target_id in problem.target_ids
        },
        stock_remaining=_stock_remaining(problem, counts),
    )


def _stock_remaining(problem: WTAProblem, counts) -> dict[str, int]:
    used: dict[tuple[str, str], int] = defaultdict(int)
    for (base_id, weapon_code, _target_id), count in counts.items():
        used[(base_id, weapon_code)] += count
    remaining: dict[str, int] = defaultdict(int)
    for (base_id, weapon_code), quantity in problem.stock.items():
        remaining[weapon_code] += quantity - used[(base_id, weapon_code)]
    return dict(sorted(remaining.items()))
