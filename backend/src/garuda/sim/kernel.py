"""Frozen sim kernel API. Sortie order is `plan.assignments`; mission order is first appearance in it."""

from dataclasses import dataclass
from statistics import NormalDist

import numpy as np

from garuda.contracts.plan import PlanDraft

WEATHER_CORRELATION = 0.6


@dataclass
class Outcomes:
    value: np.ndarray            # (n_runs,)
    mission_success: np.ndarray  # (n_runs, n_missions)
    losses: np.ndarray           # (n_runs,)


def plan_missions(plan: PlanDraft) -> list[str]:
    return list(dict.fromkeys(a.mission_id for a in plan.assignments))


def sample_launch(plan: PlanDraft, p_fmc: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    """(n_runs, n_sorties) True when the tail launches. A tail missing from `p_fmc` always launches."""
    p = np.array([p_fmc.get(a.tail_no, 1.0) for a in plan.assignments], dtype=float)
    return rng.random((n_runs, len(p))) < p


def sample_weather(plan: PlanDraft, p_go: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    """(n_runs, n_missions) True when the mission's weather is GO.

    One shared draw per run correlates missions the way one weather system would.
    A mission missing from `p_go` is always GO.
    """
    missions = plan_missions(plan)
    threshold = np.array([_normal_quantile(p_go.get(m, 1.0)) for m in missions], dtype=float)
    shared = rng.standard_normal((n_runs, 1))
    own = rng.standard_normal((n_runs, len(missions)))
    z = np.sqrt(WEATHER_CORRELATION) * shared + np.sqrt(1.0 - WEATHER_CORRELATION) * own
    return z < threshold


def sample_engagement(plan: PlanDraft, risk: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    """(n_runs, n_sorties) True when the sortie is lost. `risk` is per-sortie loss probability by mission id."""
    p = np.array([risk.get(a.mission_id, 0.0) for a in plan.assignments], dtype=float)
    return rng.random((n_runs, len(p))) < p


def simulate(plan: PlanDraft, n_runs: int, rng: np.random.Generator) -> Outcomes:
    """Plan-only prior: every tail launches, weather is GO, and each sortie carries `plan.kpis.risk`.

    A mission succeeds when at least one of its sorties survives, and is worth one unit.
    """
    missions = plan_missions(plan)
    launched = sample_launch(plan, {}, n_runs, rng)
    go = sample_weather(plan, {}, n_runs, rng)
    lost = sample_engagement(plan, {m: plan.kpis.risk for m in missions}, n_runs, rng)
    index = {m: i for i, m in enumerate(missions)}
    sortie_mission = np.array([index[a.mission_id] for a in plan.assignments], dtype=int)
    flew = launched & go[:, sortie_mission]
    success = np.zeros((n_runs, len(missions)), dtype=bool)
    for s, m in enumerate(sortie_mission):
        success[:, m] |= flew[:, s] & ~lost[:, s]
    return Outcomes(
        value=success.sum(axis=1).astype(float),
        mission_success=success,
        losses=(flew & lost).sum(axis=1).astype(float),
    )


def _normal_quantile(p: float) -> float:
    if p <= 0.0:
        return -np.inf
    if p >= 1.0:
        return np.inf
    return NormalDist().inv_cdf(p)
