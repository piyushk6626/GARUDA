"""Frozen sim kernel API. Implemented by the Monte Carlo owner; analytic stub until then."""

from dataclasses import dataclass

import numpy as np

from garuda.contracts.plan import PlanDraft


@dataclass
class Outcomes:
    value: np.ndarray            # (n_runs,)
    mission_success: np.ndarray  # (n_runs, n_missions)
    losses: np.ndarray           # (n_runs,)


def sample_launch(plan: PlanDraft, p_fmc: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    raise NotImplementedError


def sample_weather(plan: PlanDraft, p_go: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    raise NotImplementedError


def sample_engagement(plan: PlanDraft, risk: dict[str, float], n_runs: int, rng: np.random.Generator) -> np.ndarray:
    raise NotImplementedError


def simulate(plan: PlanDraft, n_runs: int, rng: np.random.Generator) -> Outcomes:
    raise NotImplementedError
