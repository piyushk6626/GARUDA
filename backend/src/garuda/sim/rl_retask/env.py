"""Gymnasium GarudaRetaskEnv: one step per open slot, masked actions for MaskablePPO."""

from collections.abc import Callable

import gymnasium as gym
import numpy as np

from garuda.sim.rl_retask.decisions import (
    OBS_DIM,
    SKIP,
    Decision,
    action_mask,
    observe,
    slot_order,
    value_scale,
)
from garuda.sim.rl_retask.problem import MAX_CANDIDATES, DecisionInstance

InstanceSource = DecisionInstance | Callable[[np.random.Generator], DecisionInstance]


class GarudaRetaskEnv(gym.Env):
    """Reward for a fill is slot value times success probability, scaled by the largest slot value."""

    metadata = {"render_modes": []}

    def __init__(self, source: InstanceSource) -> None:
        self.source = source
        self.observation_space = gym.spaces.Box(0.0, 1.0, shape=(OBS_DIM,), dtype=np.float32)
        self.action_space = gym.spaces.Discrete(MAX_CANDIDATES + 1)
        self.instance: DecisionInstance | None = None
        self.order: list[int] = []
        self.step_idx = 0
        self.used = np.zeros(0, dtype=bool)
        self.decision: Decision = []

    def reset(self, *, seed: int | None = None, options: dict | None = None):
        super().reset(seed=seed)
        self.instance = self.source(self.np_random) if callable(self.source) else self.source
        if not len(self.instance.values):
            raise ValueError("a retask episode needs at least one slot")
        self.order = slot_order(self.instance)
        self.step_idx = 0
        self.used = np.zeros(self.instance.feasible.shape[1], dtype=bool)
        self.decision = [None] * len(self.instance.values)
        return self._obs(), {}

    def step(self, action: int):
        slot = self.order[self.step_idx]
        reward = 0.0
        if action != SKIP and self.action_masks()[action]:
            self.used[action] = True
            self.decision[slot] = int(action)
            reward = self.instance.values[slot] * self.instance.p_success[slot, action] / value_scale(self.instance)
        self.step_idx += 1
        terminated = self.step_idx >= len(self.order)
        obs = np.zeros(OBS_DIM, dtype=np.float32) if terminated else self._obs()
        return obs, float(reward), terminated, False, {"decision": list(self.decision)}

    def action_masks(self) -> np.ndarray:
        return action_mask(self.instance, self.order[self.step_idx], self.used)

    def _obs(self) -> np.ndarray:
        return observe(self.instance, self.step_idx, self.order[self.step_idx], self.used)


def random_instance(rng: np.random.Generator) -> DecisionInstance:
    """Synthetic retask for training: a few slots, a sparse feasibility matrix, mixed success odds."""
    n_slots = int(rng.integers(1, 7))
    n_cand = int(rng.integers(2, MAX_CANDIDATES + 1))
    return DecisionInstance(
        values=rng.uniform(1.0, 10.0, n_slots),
        priorities=rng.integers(1, 6, n_slots).astype(float),
        feasible=rng.random((n_slots, n_cand)) < 0.4,
        p_success=rng.uniform(0.4, 1.0, (n_slots, n_cand)),
        distance_km=rng.uniform(50.0, 1500.0, (n_slots, n_cand)),
    )
