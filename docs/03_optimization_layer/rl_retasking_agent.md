# RL Retasking Agent (Stretch Goal)

Tool: `rl_retask`

## Problem
Learn a policy that reacts to sequences of events (failures, new threats, requests) faster than a full re-solve and handles stochastic futures. Used as an alternative/ensemble to `retask_rolling_horizon`, compared head to head.

## Environment (Gymnasium `GarudaRetaskEnv`)
- **State**: per-aircraft features (status, base, fuel, capability one-hot, p_fail), per-mission features (priority, window remaining, required capabilities, assigned?), base capacity, weather flags, threat risk per mission; encoded as padded tensors / graph.
- **Action**: pick (aircraft, mission) pair or "skip" at each decision step (discrete with action masking for infeasible pairs; large action space handled via masking plus attention policy).
- **Transition**: SimPy-lite simulator step: time advances to next decision point, random events from scenario generator.
- **Reward**: `+v_m · success` on completion, `−c · loss`, `−λ · changes_to_frozen_plan`, small shaping reward for assigning high-priority early, `−penalty` for missed windows.
- **Episode**: 72 h scenario with random events; 100-300 decisions.

## Algorithm
PPO (Stable-Baselines3 `MaskablePPO` from sb3-contrib), 2-layer MLP or transformer encoder over entities. Curriculum: small (10 aircraft, 8 missions) -> theatre size. Warm start by imitation learning (behaviour cloning) on MILP solutions (DAgger optional) for sample efficiency.

## Pseudocode
```
env = make_env(seed, curriculum_level)
model = MaskablePPO("MlpPolicy", env, n_steps=2048, gamma=0.995, ...)
pretrain(model, expert=milp_solutions)
model.learn(total_timesteps=5e6)
evaluate(model, scenarios=50, baselines=[milp_rolling, greedy])
```

## Tool signature
```python
class RLRetaskInput(BaseModel):
    plan_id: str
    event: Event
    deterministic: bool = True

class RLRetaskOutput(BaseModel):
    new_plan_id: str
    diff: PlanDiff
    policy_version: str
    decision_latency_ms: float
    compare_with_milp: dict[str, float] | None = None
```
Output is always validated by the constraint checker; infeasible RL actions are repaired or replaced by MILP.

## Evaluation
Mean episode value vs MILP baseline, constraint violation rate (must be 0 after validation), latency, robustness to unseen events.

## Risks
Sim-to-real gap, reward hacking, explainability gaps; hence only advisory, always compared with solver. Not recommended for the first demo; prioritise only after MILP/CP-SAT core works.

## Libraries
gymnasium, stable-baselines3, sb3-contrib, torch, SimPy.
