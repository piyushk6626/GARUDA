"""Aggregate simulation outcomes into the unchanged public output schema."""

import numpy as np

from garuda.contracts.results import Distribution, Sensitivity
from garuda.sim.evaluate_plan_monte_carlo.model import Solution
from garuda.sim.evaluate_plan_monte_carlo.problem import Problem
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloOutput


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks for ties, without introducing a SciPy dependency."""
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    return (ends - (counts - 1) / 2)[inverse]


def _spearman(left: np.ndarray, right: np.ndarray) -> float:
    a, b = _ranks(left), _ranks(right)
    a -= a.mean()
    b -= b.mean()
    scale = float(np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return float(np.clip(np.dot(a, b) / scale, -1, 1)) if scale else 0.0


def explain(problem: Problem, solution: Solution) -> EvaluatePlanMonteCarloOutput:
    p5, p50, p95 = np.percentile(solution.value, [5, 50, 95])
    # Average precisely the worst 5% mass, including fractional boundary mass.
    # value <= p5 would include too many samples for discrete outcomes.
    ordered = np.sort(solution.value)
    mass = len(ordered) * 0.05
    whole = int(mass)
    tail_sum = float(ordered[:whole].sum())
    if whole < len(ordered):
        tail_sum += (mass - whole) * float(ordered[whole])
    probabilities = solution.mission_success.mean(axis=0)
    mission_success = {m.mission_id: float(p) for m, p in zip(problem.missions, probabilities)}
    sensitivities = sorted(
        (Sensitivity(factor=name, effect=_spearman(values, solution.value))
         for name, values in solution.factors.items()),
        key=lambda sensitivity: (-abs(sensitivity.effect), sensitivity.factor))
    weakest = sorted((m.mission_id for m in problem.missions
                      if mission_success[m.mission_id] < m.min_pk),
                     key=lambda mid: (mission_success[mid], mid))
    return EvaluatePlanMonteCarloOutput(
        value=Distribution(mean=float(solution.value.mean()), p5=float(p5), p50=float(p50), p95=float(p95)),
        mission_success=mission_success,
        expected_losses=float(solution.losses.mean()),
        p_value_above_threshold=float(np.mean(solution.value >= problem.threshold)),
        cvar_5=tail_sum / mass,
        sensitivities=sensitivities,
        weakest_links=weakest,
    )
