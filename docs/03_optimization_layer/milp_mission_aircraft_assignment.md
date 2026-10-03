# MILP Mission-Aircraft Assignment

Tool: `assign_missions`

## Problem
Given missions with priorities, time windows and capability needs, and aircraft with availability, location, load options and crew, choose which aircraft (with which load config) fly which missions to maximise priority-weighted value, respecting weapons, range, and base sortie limits.

## Formulation
**Sets**: M missions, A aircraft, C load configs, T time buckets (6 h), B bases.
**Parameters**: `v_m` value (mission priority points); `req_m` aircraft required (min `n_m^min`, max `n_m^max`); `ok_{a,c,m}` 1 if aircraft a with config c is capable for m (capability, range incl. AAR, weather go); `avail_{a,t}` availability (FMC and forecast); `w_{c,k}` count of weapon k in config c; `S_{b,k}` stock; `cap_{b,t}` sortie capacity; `pk_{c,m}` estimated effect probability; `r_{a,m}` risk (threat exposure); `λ` risk weight.
**Variables**: `x_{a,c,m,t} ∈ {0,1}` assign; `y_m ∈ {0,1}` mission fulfilled.

**Objective**
max Σ_m v_m · y_m + Σ x · μ · pk_{c,m} − λ Σ x · r_{a,m}

**Constraints**
1. Each aircraft at most one sortie per turnaround window: Σ_{c,m,t' ∈ W(t)} x_{a,c,m,t'} ≤ avail_{a,t}.
2. Capability: x_{a,c,m,t} ≤ ok_{a,c,m}.
3. Package size: n_m^min · y_m ≤ Σ_{a,c,t} x ≤ n_m^max · y_m.
4. Weapons: Σ_{a,c,m,t: base(a)=b} w_{c,k} x ≤ S_{b,k} − reserved.
5. Base sortie capacity: Σ_{a: base(a)=b} Σ x_{·,t} ≤ cap_{b,t}.
6. Time window: x only defined for t feasible given launch-to-TOT travel time.
7. Dependencies: y_{strike} ≤ y_{sead} for linked packages.
8. Crew link: Σ x ≤ crew_available (or tied to `schedule_crew` result).
9. Tanker/AAR requirement: mission needing AAR requires y_aar = 1.

## Data inputs
`aircraft`, `crew`, `weapon_stock`, `load_config`, `mission`, `weather` (go/no-go), `threat` (risk), `base` (capacity).

## Library
PuLP with HiGHS/CBC, or OR-Tools `pywraplp` (SCIP/CBC). Preprocess to drop infeasible tuples (x variables count ~ 150 x 5 x 50 x 12 = 450k worst case; pruned to ~30-60k).

## Pseudocode
```
build ok-tuples by filtering
create variables only for ok-tuples
add objective and constraints
solve(time_limit, gap=1%)
extract assignments; compute unassigned reasons via LP relaxation duals / per-mission infeasibility checks
```

## Tool signature
```python
class AssignMissionsInput(BaseModel):
    scenario_time: datetime
    horizon_hours: int = 72
    mission_ids: list[str] | None = None
    weights: ObjectiveWeights = ObjectiveWeights(risk=0.2, effect=0.3)
    locked_assignments: list[Assignment] = []
    time_limit_s: int = 30

class AssignMissionsOutput(BaseModel):
    plan_id: str
    status: Literal["OPTIMAL","FEASIBLE","INFEASIBLE"]
    assignments: list[Assignment]    # aircraft, config, mission, depart, tot
    unassigned: list[UnassignedMission]   # with reason code
    kpis: PlanKPIs                   # value, fulfilment %, sorties, risk
    binding_constraints: list[str]
```

## Complexity
NP-hard; practical for theatre scale with pruning (seconds to <1 min). Gap-limited.

## Explainability
Value breakdown per mission, binding constraints (weapon stock at B2, tanker capacity), sensitivity: shadow prices from LP relaxation.

## Failure modes
Infeasible -> relax min package size; timeout -> incumbent plus gap; stale data -> warn.

## Validation
Independent plan validator; compare against greedy baseline; unit tests on toy instances with known optimum.
