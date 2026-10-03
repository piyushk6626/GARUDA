# Component Failure Survival Analysis

Tool: `predict_component_rul`

## Problem
Estimate remaining useful life (RUL) and failure probability over the next N flight hours for key components (engine, radar, hydraulics, avionics) to schedule maintenance and avoid mid-mission failure.

## Models
1. **Weibull AFT / parametric**: `S(t) = exp(−(t/λ)^k)`, k>1 wear-out. Fit per component type with lifelines `WeibullAFTFitter` with covariates (type, operating tempo, environment: heat/dust).
2. **Cox Proportional Hazards**: `h(t|x) = h0(t) exp(βᵀx)` using lifelines `CoxPHFitter`; handles right-censored components.
3. Optional: Random Survival Forest (scikit-survival) for non-proportional effects.

## Data
Component log: `component_id, tail_no, type, hours_since_overhaul, failed (0/1), covariates (sortie intensity, g-load proxy, climate)`. Synthetic generated from known Weibull parameters (k=2.2, λ=800 h for engine, etc.) with 40-60% censoring, so recovery of parameters can be tested.

## Outputs
- Conditional failure probability within next h hours: `P(fail ∈ (t, t+h] | survived t) = 1 − S(t+h)/S(t)`.
- RUL quantiles (p10/p50/p90) = time when conditional survival hits thresholds.

## Pseudocode
```
fit cph on historical component records
for each component: S = predict_survival_function(x, times=t+grid)
cond = S(t+h)/S(t); p_fail = 1 - cond
aircraft_risk = 1 - prod(1 - p_fail_c over critical components)
```

## Tool signature
```python
class RULInput(BaseModel):
    tail_nos: list[str] | None = None
    sortie_hours_ahead: float = 6.0
    component_types: list[str] | None = None

class RULOutput(BaseModel):
    items: list[ComponentRisk]   # tail, component, hours_since_overhaul, p_fail_next, rul_p10_p50_p90
    aircraft_risk: dict[str, float]
    recommended_maintenance: list[MaintenanceAdvice]
```
Feeds availability forecast feature `aircraft_risk` and mission assignment (avoid high-risk aircraft for deep strikes).

## Validation
Concordance index, calibration of predicted vs observed failure fractions, parameter recovery on synthetic data.

## Failure modes
Few events -> wide intervals, flagged; proportional-hazard check via Schoenfeld residuals.
