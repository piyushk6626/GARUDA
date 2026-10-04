"""explain(problem, simulation) -> EvaluatePlanMonteCarloOutput. See docs/03_optimization_layer/monte_carlo_plan_evaluation.md."""

import numpy as np

from garuda.contracts.results import Distribution, Sensitivity
from garuda.sim.evaluate_plan_monte_carlo.model import Simulation
from garuda.sim.evaluate_plan_monte_carlo.problem import MonteCarloProblem
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloOutput

MAX_WEAKEST_LINKS = 5


def explain(problem: MonteCarloProblem, simulation: Simulation) -> EvaluatePlanMonteCarloOutput:
    outcomes = simulation.outcomes
    value = outcomes.value
    p5, p50, p95 = np.percentile(value, [5, 50, 95])
    success = outcomes.mission_success.mean(axis=0)
    return EvaluatePlanMonteCarloOutput(
        value=Distribution(mean=float(value.mean()), p5=float(p5), p50=float(p50), p95=float(p95)),
        mission_success={m.mission_id: float(p) for m, p in zip(problem.missions, success)},
        expected_losses=float(outcomes.losses.mean()),
        p_value_above_threshold=float((value >= problem.value_threshold).mean()),
        cvar_5=float(value[value <= p5].mean()),
        sensitivities=_sensitivities(value, simulation.factors),
        weakest_links=_weakest_links(problem, success),
    )


def _sensitivities(value: np.ndarray, factors: dict[str, np.ndarray]) -> list[Sensitivity]:
    """Spearman rank correlation of plan value with each sampled factor, largest effect first."""
    found = [
        Sensitivity(factor=name, effect=round(_spearman(samples, value), 4))
        for name, samples in factors.items()
        if np.ptp(samples) > 0 and np.ptp(value) > 0
    ]
    return sorted(found, key=lambda s: abs(s.effect), reverse=True)


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = _ranks(x), _ranks(y)
    return float(np.corrcoef(rx, ry)[0, 1])


def _ranks(x: np.ndarray) -> np.ndarray:
    """Average ranks, so ties (common in 0/1 factors) do not bias the correlation."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(len(x), dtype=float)
    _, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    return (sums / counts)[inverse]


def _weakest_links(problem: MonteCarloProblem, success: np.ndarray) -> list[str]:
    """Missions that cost the most expected value, among those that sometimes fail."""
    lost = [(m.value * (1.0 - p), m.mission_id) for m, p in zip(problem.missions, success) if p < 1.0]
    return [mission_id for _, mission_id in sorted(lost, reverse=True)[:MAX_WEAKEST_LINKS]]
