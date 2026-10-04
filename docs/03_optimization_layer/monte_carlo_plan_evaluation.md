# Monte Carlo Plan Evaluation

Tool: `evaluate_plan_monte_carlo`

## Problem
A deterministic plan hides uncertainty: aircraft failures, weather deviation, threat effectiveness, delays. Simulate the plan N times to get distributions of outcomes and robustness before approval.

## Simulation model
For each replication r:
1. Sample aircraft status at launch: Bernoulli with `p_i` from availability forecast / RUL (ground abort -> spare aircraft used if available).
2. Sample weather outcome from `predict_weather_impact` (GO/DEGRADED/NO_GO; weather correlated spatially via shared draw per day).
3. Sample delays: turnaround ~ LogNormal; TOT slip.
4. Engagement: for each mission leg traversing threat envelopes, loss probability per exposure = `1 − Π(1 − pk_i · conf_i · exposure_time_i)`; sample attrition. Realised threat strength: sample `conf` and Pk with Beta noise.
5. Weapon effect: sample Bernoulli(Pk) for each weapon on target; target destroyed if ≥ 1 hit (or damage fraction model).
6. Cascades: lost SEAD increases strike risk; tanker failure drops AAR-dependent missions.
7. Record outcomes: mission success, losses, weapons used, value achieved.

## Outputs
Mean, std, percentiles (p5, p50, p95) for total value, per-mission success probability, expected losses, probability that value ≥ threshold, CVaR_5 of value, sensitivity (Spearman/ tornado) to inputs.

## Pseudocode
```
rng = default_rng(seed); outcomes = []
for r in range(N):
    state = sample_initial_state(plan, rng)
    for event in time_ordered(plan):
        resolve(event, state, rng)
    outcomes.append(summarise(state))
stats = aggregate(outcomes)
```
Vectorised with NumPy across replications (N=5,000-20,000); parallel via `joblib`.

## Tool signature
```python
class MonteCarloInput(BaseModel):
    plan_id: str
    n_runs: int = 5000
    seed: int = 42
    stress: Literal["none","degraded_weather","heavy_threat","tanker_loss"] = "none"
    value_threshold: float | None = None   # default: 80% of planned value

class MonteCarloOutput(BaseModel):
    value: Distribution        # mean, p5, p50, p95
    mission_success: dict[str, float]
    expected_losses: float
    p_value_above_threshold: float
    cvar_5: float
    sensitivities: list[Sensitivity]
    weakest_links: list[str]
```

## Uses
Approval-gate risk report; alternative comparison in `pareto_tradeoffs`; training data for `mission_success_probability`.

## Complexity
5,000 runs x ~50 missions in vectorised form: ~1-3 s.

## Validation
Convergence plots (standard error), analytic checks for simple cases (single mission, one SAM), seed reproducibility.
