# Weapon-Target Assignment (WTA)

Tool: `assign_weapons_to_targets`

## Problem
Allocate weapons (by type and platform) to targets to maximise expected destroyed target value, accounting for non-linear kill probability when several weapons hit one target, with stock and platform limits.

## Formulation (static WTA)
**Sets**: W weapons (units, by type k), T targets.
**Parameters**: `V_t` value; `p_{k,t}` single-shot kill prob (Pk, from weapon catalog adjusted by hardness, threat, weather); `N_k` stock available; `L_{a}` station capacity of platform a.
**Variables**: `x_{k,t} ∈ Z≥0` number of weapons of type k on target t.

**Objective (nonlinear)**: min Σ_t V_t · Π_k (1 − p_{k,t})^{x_{k,t}}  (expected surviving value)
equivalently max Σ_t V_t (1 − Π_k (1−p_{k,t})^{x_{k,t}}).

**Constraints**: Σ_t x_{k,t} ≤ N_k; Σ_{k,t via platform a} x ≤ L_a; x_{k,t} ≤ x_max; optional min coverage for priority targets (1 − survive_t ≥ θ_t).

## Linearisation (MILP)
Take logs: `log S_t = Σ_k x_{k,t} · log(1−p_{k,t})`, and survival `S_t = exp(·)` is convex in x; piecewise-linear approximate with binary expansion `x_{k,t} = Σ_j j · b_{k,t,j}` (j = 0..J, J≈4) with SOS1 over j; survival per target = piecewise-linear over `u_t = Σ_k x_{k,t} ln(1−p_{k,t})` using lambda-formulation (SOS2). Solve with MILP.

## Heuristic (default for speed)
Greedy marginal-return: repeatedly assign the next weapon with the largest `V_t · S_t · p_{k,t}` per unit cost (diminishing returns makes greedy near optimal for this submodular objective: guarantee (1 − 1/e) for the matroid-constrained case). Followed by local swap improvement.

## Pseudocode (greedy)
```
S_t = 1 for all targets
while any stock:
  best = argmax_{k,t} V_t * S_t * p[k,t] / cost[k]
  if gain < eps: break
  x[best]++; S_t *= (1 - p[best]); stock[k]--
local_search(swap pairs)
```

## Tool signature
```python
class WTAInput(BaseModel):
    target_ids: list[str]
    base_ids: list[str] | None = None
    method: Literal["greedy","milp"] = "greedy"
    min_destroy_prob: dict[str, float] = {}
    scenario_time: datetime

class WTAOutput(BaseModel):
    allocations: list[WeaponAllocation]   # target, weapon_code, count, base
    expected_destroyed_value: float
    kill_prob_by_target: dict[str, float]
    stock_remaining: dict[str, int]
```

## Data inputs
`weapon_type.pk_by_target_class`, `weapon_stock`, `target`, `threat` (degradation), `weather` (guidance penalty).

## Complexity
Greedy O(n log n); MILP with piecewise terms scales to ~50 targets x 14 types.

## Failure modes
Insufficient stock -> report shortfall per target; Pk uncertainty -> use lower confidence bound variant (robust WTA).

## Validation
Compare greedy vs MILP gap on random instances; Monte Carlo check of kill probabilities.
