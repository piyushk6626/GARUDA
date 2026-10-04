"""Analytic mission success with independent sorties and no cascades: fallback and test oracle.

Exact for a mission without dependencies, SEAD suppression, or an AAR tanker mission in the plan,
when it needs one aircraft or does not need a kill.
"""

import numpy as np

from garuda.sim.evaluate_plan_monte_carlo.problem import MonteCarloProblem


def baseline(problem: MonteCarloProblem) -> dict[str, float]:
    pk = np.array([t.pk for t in problem.threats], dtype=float)
    confidence = np.array([t.confidence for t in problem.threats], dtype=float)
    tanker = 0.0 if problem.tanker_missions else problem.p_tanker
    arrive: dict[int, list[float]] = {}
    kill: dict[int, list[float]] = {}
    for sortie in problem.sorties:
        exposure = np.asarray(sortie.exposure, dtype=float)
        survive = float(np.prod(1.0 - np.minimum(0.95, pk * confidence * exposure)))
        p = sortie.p_launch * survive * (tanker if sortie.needs_aar else 1.0)
        arrive.setdefault(sortie.mission_idx, []).append(p)
        kill.setdefault(sortie.mission_idx, []).append(p * sortie.kill_pk)
    result: dict[str, float] = {}
    for m, mission in enumerate(problem.missions):
        enough = _at_least(arrive.get(m, []), mission.min_aircraft)
        if mission.needs_kill:
            hit = 1.0 - float(np.prod([1.0 - p for p in kill.get(m, [])]))
            enough = hit if mission.min_aircraft <= 1 else min(enough, hit)
        result[mission.mission_id] = mission.p_go * enough
    return result


def _at_least(probabilities: list[float], k: int) -> float:
    """P(at least k successes) for independent Bernoulli trials (Poisson-binomial)."""
    counts = np.zeros(len(probabilities) + 1)
    counts[0] = 1.0
    for p in probabilities:
        counts[1:] = counts[1:] * (1.0 - p) + counts[:-1] * p
        counts[0] *= 1.0 - p
    return float(counts[k:].sum())
