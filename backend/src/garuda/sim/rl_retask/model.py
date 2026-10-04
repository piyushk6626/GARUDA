"""Run a retask policy, compare it with the exact MILP repair, and train the MaskablePPO policy."""

from collections.abc import Callable

import numpy as np

from garuda.contracts.tool import ToolContext
from garuda.sim.rl_retask.baseline import GREEDY_VERSION, Policy, greedy_policy
from garuda.sim.rl_retask.decisions import SKIP, Decision, action_mask, observe, slot_order
from garuda.sim.rl_retask.problem import DecisionInstance

POLICY_NAME = "rl_retask"
PPO_VERSION = "maskable-ppo"
MILP_SCALE = 10_000
MILP_TIME_LIMIT_S = 5.0


def load_policy(ctx: ToolContext, deterministic: bool) -> tuple[Policy, str]:
    """The trained MaskablePPO policy from the model store, else the masked greedy policy."""
    try:
        model = ctx.models.load(POLICY_NAME)
    except Exception:
        model = None
    if model is None or not hasattr(model, "predict"):
        return greedy_policy(deterministic, ctx.seed), GREEDY_VERSION

    def act(obs: np.ndarray, mask: np.ndarray) -> int:
        action, _ = model.predict(obs, action_masks=mask, deterministic=deterministic)
        return int(action)

    return act, str(getattr(model, "policy_version", PPO_VERSION))


def run_policy(instance: DecisionInstance, policy: Policy) -> Decision:
    """Decide each slot in priority order. Masked-out picks are treated as a skip."""
    decision: Decision = [None] * len(instance.values)
    used = np.zeros(instance.feasible.shape[1], dtype=bool)
    for step, slot in enumerate(slot_order(instance)):
        mask = action_mask(instance, slot, used)
        action = policy(observe(instance, step, slot, used), mask)
        if action != SKIP and mask[action]:
            used[action] = True
            decision[slot] = action
    return decision


def solve_milp(instance: DecisionInstance) -> Decision | None:
    """Exact repair: maximise value times success, one tail per slot and one slot per tail. None without OR-Tools."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None
    n_slots, n_cand = instance.feasible.shape
    model = cp_model.CpModel()
    x = {
        (s, a): model.NewBoolVar(f"x_{s}_{a}")
        for s in range(n_slots)
        for a in range(n_cand)
        if instance.feasible[s, a]
    }
    for s in range(n_slots):
        model.AddAtMostOne(x[s, a] for a in range(n_cand) if (s, a) in x)
    for a in range(n_cand):
        model.AddAtMostOne(x[s, a] for s in range(n_slots) if (s, a) in x)
    model.Maximize(
        sum(round(MILP_SCALE * instance.values[s] * instance.p_success[s, a]) * var for (s, a), var in x.items())
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = MILP_TIME_LIMIT_S
    solver.parameters.num_workers = 1
    if solver.Solve(model) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    decision: Decision = [None] * n_slots
    for (s, a), var in x.items():
        if solver.Value(var):
            decision[s] = a
    return decision


def train_policy(
    source: DecisionInstance | Callable[[np.random.Generator], DecisionInstance] | None = None,
    total_timesteps: int = 200_000,
    seed: int = 42,
):
    """Train MaskablePPO on retask episodes. Store the result with `ctx.models.save(POLICY_NAME, model, ...)`."""
    from sb3_contrib import MaskablePPO

    from garuda.sim.rl_retask.env import GarudaRetaskEnv, random_instance

    env = GarudaRetaskEnv(source if source is not None else random_instance)
    model = MaskablePPO("MlpPolicy", env, gamma=0.995, n_steps=512, batch_size=64, seed=seed, verbose=0)
    model.learn(total_timesteps=total_timesteps)
    model.policy_version = f"{PPO_VERSION}-{total_timesteps}"
    return model
