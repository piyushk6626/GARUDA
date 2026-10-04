"""Observation, action mask, and scoring shared by the env, the policies, and the MILP comparison.

Slots are decided one at a time, highest priority first. An action picks a candidate column or
skips (the last action). A tail takes at most one new sortie per retask.
"""

import numpy as np

from garuda.sim.rl_retask.problem import MAX_CANDIDATES, DecisionInstance

SLOT_FEATURES = 3
CANDIDATE_FEATURES = 4
OBS_DIM = SLOT_FEATURES + MAX_CANDIDATES * CANDIDATE_FEATURES
SKIP = MAX_CANDIDATES
DISTANCE_SCALE_KM = 2000.0

Decision = list[int | None]


def slot_order(instance: DecisionInstance) -> list[int]:
    return sorted(range(len(instance.values)), key=lambda s: (instance.priorities[s], -instance.values[s]))


def value_scale(instance: DecisionInstance) -> float:
    return float(instance.values.max()) if len(instance.values) and instance.values.max() > 0 else 1.0


def action_mask(instance: DecisionInstance, slot: int, used: np.ndarray) -> np.ndarray:
    mask = np.zeros(MAX_CANDIDATES + 1, dtype=bool)
    n = instance.feasible.shape[1]
    mask[:n] = instance.feasible[slot] & ~used
    mask[SKIP] = True
    return mask


def observe(instance: DecisionInstance, step: int, slot: int, used: np.ndarray) -> np.ndarray:
    scale = value_scale(instance)
    n_slots = len(instance.values)
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    obs[0] = instance.priorities[slot] / 5.0
    obs[1] = instance.values[slot] / scale
    obs[2] = (n_slots - step) / max(1, n_slots)
    n = instance.feasible.shape[1]
    open_ = instance.feasible[slot] & ~used
    cols = obs[SLOT_FEATURES:].reshape(MAX_CANDIDATES, CANDIDATE_FEATURES)
    cols[:n, 0] = open_
    cols[:n, 1] = instance.p_success[slot] * open_
    cols[:n, 2] = instance.values[slot] * instance.p_success[slot] * open_ / scale
    cols[:n, 3] = np.minimum(instance.distance_km[slot] / DISTANCE_SCALE_KM, 1.0)
    return obs


def decision_value(instance: DecisionInstance, decision: Decision) -> float:
    return float(sum(instance.values[s] * instance.p_success[s, a] for s, a in enumerate(decision) if a is not None))


def filled(decision: Decision) -> int:
    return sum(1 for a in decision if a is not None)
