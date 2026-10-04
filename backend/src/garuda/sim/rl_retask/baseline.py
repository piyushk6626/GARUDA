"""Masked greedy policy: the fallback when no trained policy is stored, and the RL test oracle.

It reads the same observation as the RL policy and picks the open candidate with the highest
value times success probability. Stochastic mode samples in proportion to that score.
"""

from collections.abc import Callable

import numpy as np

from garuda.sim.rl_retask.decisions import CANDIDATE_FEATURES, SKIP, SLOT_FEATURES
from garuda.sim.rl_retask.problem import MAX_CANDIDATES

Policy = Callable[[np.ndarray, np.ndarray], int]
GREEDY_VERSION = "greedy-masked-1"


def greedy_policy(deterministic: bool = True, seed: int = 42) -> Policy:
    rng = np.random.default_rng(seed)

    def act(obs: np.ndarray, mask: np.ndarray) -> int:
        score = obs[SLOT_FEATURES:].reshape(MAX_CANDIDATES, CANDIDATE_FEATURES)[:, 2] * mask[:SKIP]
        if score.max() <= 0:
            return SKIP
        if deterministic:
            return int(np.argmax(score))
        return int(rng.choice(MAX_CANDIDATES, p=score / score.sum()))

    return act
