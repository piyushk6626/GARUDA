"""MILP comparator on the same event-aware candidates and frozen constraints."""

from garuda.optimization.retask_rolling_horizon.model import solve


def baseline(problem):
    return solve(problem.retask)
