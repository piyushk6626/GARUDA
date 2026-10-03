# LP Sortie Generation Rate

Tool: `compute_sortie_capacity`

## Problem
Determine how many sorties each base can generate per period given aircraft, turnaround times, ground crew, fuel, munitions, runway throughput and shelter limits. Also tells the planner the surge limit and bottleneck.

## Formulation
**Sets**: B bases, P periods (1 h), G aircraft groups (type x base).
**Parameters**: `n_{g,p}` mission-capable aircraft; `τ_g` turnaround time (h); `u_g` ground-crew team-hours per turnaround; `E_{b,p}` team-hours available; `f_g` fuel per sortie; `F_{b}` fuel stock + resupply; `m_{g,k}` munitions per sortie; `M_{b,k}`; `R_b` runway throughput (ops/h, take-off+landing); `fatigue/ crew limit C_{b,p}`.
**Variables**: `s_{g,p} ≥ 0` sorties launched (continuous relaxation, ok at fleet scale).

**Objective**: max Σ_{g,p} w_g s_{g,p} (w_g capability weight) — or, for demand-driven mode, minimise unmet demand `Σ_p d_p − Σ s`.

**Constraints**
1. Aircraft: Σ_{p' ∈ [p−τ_g+1, p]} s_{g,p'} ≤ n_{g,p}.
2. Ground crew: Σ_g u_g s_{g,p} ≤ E_{b,p}.
3. Fuel (cumulative): Σ_{p'≤p} Σ_g f_g s_{g,p'} ≤ F_b(p).
4. Munitions (cumulative): Σ_{p'≤p} Σ_g m_{g,k} s ≤ M_{b,k}(p).
5. Runway: Σ_g 2 s_{g,p} ≤ R_b.
6. Crew: Σ_g s_{g,p} ≤ C_{b,p}.
7. Weather: s = 0 when base closed.

## Library
PuLP/HiGHS `LpProblem` (pure LP, solves in milliseconds); dual values give bottleneck (shadow prices).

## Pseudocode
```
build LP per base (independent) -> solve -> read duals
bottleneck = argmax |dual| among {aircraft, ground_crew, fuel, munitions, runway, crew}
```

## Tool signature
```python
class SortieCapacityInput(BaseModel):
    base_ids: list[str] | None = None
    start: datetime
    horizon_hours: int = 24
    surge: bool = False     # raises ground crew hours, relaxes turnaround by 15%

class SortieCapacityOutput(BaseModel):
    capacity: dict[str, list[int]]        # base -> sorties per hour
    totals: dict[str, int]
    bottlenecks: dict[str, str]
    shadow_prices: dict[str, dict[str, float]]
```

## Uses
Feeds `cap_{b,t}` into MILP assignment; lets the LLM answer "how many more sorties if we add 2 ground crew teams?" by re-solving with changed parameters (what-if).

## Failure modes
Fractional solution rounded down per period; show rounding loss.

## Validation
Compare with SimPy turnaround simulation of one base.
