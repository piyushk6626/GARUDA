# Multi-Objective Trade-offs

Tool: `pareto_tradeoffs`

## Problem
Commanders balance competing objectives: mission value, risk to aircrew, resource use (fuel/munitions), fatigue, and plan stability. Provide a small set of Pareto-optimal plans instead of one.

## Objectives
f1 = mission value (max), f2 = cumulative risk (min), f3 = munitions expended (min), f4 = max crew fatigue (min), f5 = disruption vs. current plan (min).

## Methods
1. **Weighted sum**: scalarise `Σ w_i f_i`; vary weights over a simplex grid. Finds only supported (convex) Pareto points.
2. **Epsilon-constraint**: optimise f1 s.t. f2 ≤ ε2, f4 ≤ ε4,...; sweep ε values; finds non-convex points. Use augmented (AUGMECON) version to avoid weakly Pareto solutions.
3. **NSGA-II** (pymoo) as optional for black-box evaluation (e.g. Monte Carlo based objective); population 100, 50 generations.

## Procedure
```
1. solve each objective alone -> ideal and nadir points
2. choose k (5) epsilon grid values for risk
3. for each eps: solve MILP with risk <= eps (warm-start from neighbour)
4. filter dominated; cluster to <= 5 representative plans (k-medoids on normalised objectives)
5. label them: "Max effect", "Balanced", "Min risk", "Min disruption"
```

## Tool signature
```python
class ParetoInput(BaseModel):
    base_plan_id: str
    objectives: list[Literal["value","risk","munitions","fatigue","disruption"]] = ["value","risk"]
    n_points: int = 5
    method: Literal["epsilon","weighted","nsga2"] = "epsilon"
    time_limit_s_per_solve: int = 15

class ParetoOutput(BaseModel):
    options: list[PlanOption]     # plan_id, label, objective vector, key differences
    ideal: dict[str, float]
    nadir: dict[str, float]
    recommendation: str | None
```

## Explainability
Table of options with marginal rates ("+12 value costs +0.04 risk"); highlights knee point (max distance from line joining extremes).

## Library
PuLP/OR-Tools (epsilon), pymoo (NSGA-II), scikit-learn (clustering).

## Complexity
k x single MILP solves; parallelisable across processes.

## Failure modes
Degenerate frontier (one dominating plan) -> report single plan. Infeasible epsilon -> skip and note.

## Validation
Check non-dominance; hypervolume indicator; reproducibility with seeds.
