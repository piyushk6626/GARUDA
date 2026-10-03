# Rolling-Horizon Dynamic Retasking

Tool: `retask_rolling_horizon`

## Problem
When an event occurs (aircraft NMC, new SAM, base closure, new P1 mission, tanker loss), repair the current plan quickly, changing as little as necessary for missions already launched or imminent.

## Approach
Re-solve the assignment MILP over a rolling horizon with:
- **Frozen window**: missions starting within `H_freeze` (e.g. 2 h) or already airborne are fixed (`locked_assignments`).
- **Warm start**: previous solution supplied as MIP start.
- **Stability penalty**: add `− κ · Σ changes` where change = `x_new ≠ x_old` (linearised with `c_{a,m} ≥ x_old − x_new`).
- **Event handling** translates the event to model changes:
| Event | Model change |
|---|---|
| `aircraft_nmc` | avail_{a,t}=0 from t_event until ETR |
| `new_sam` | update risk raster; recompute r_{a,m}; mark routes needing replan |
| `base_closed` | cap_{b,t}=0; aircraft diverted; launch base alternatives |
| `p1_request` | add mission with high v_m; may preempt lower-priority |
| `tanker_lost` | remove tanker; re-run AAR scheduling; missions needing AAR may drop |

## Formulation
Same as `milp_mission_aircraft_assignment` plus:
Objective: max Σ value − κ Σ_{a,m} c_{a,m} − ρ · preemption penalty.
Constraints: `x_{a,c,m,t} = x^{old}` for frozen set; optional "minimum disruption mode": fix everything not touched by the event, solve only affected neighbourhood (LNS).

## Pseudocode
```
affected = impacted_missions(event, plan)
frozen = airborne ∪ within freeze window
mode FAST: free only affected ∪ candidates for replacement  (LNS)
solve small MILP (warm start, 10 s)
if gain > threshold or infeasible: widen neighbourhood, solve again (30 s)
return new plan + diff
```

## Tool signature
```python
class RetaskInput(BaseModel):
    plan_id: str
    event: Event      # type, entity_id, params, time
    freeze_hours: float = 2.0
    stability_weight: float = 5.0
    mode: Literal["fast","full"] = "fast"
    time_limit_s: int = 15

class RetaskOutput(BaseModel):
    new_plan_id: str
    diff: PlanDiff            # added, removed, changed assignments
    missions_degraded: list[str]
    kpi_delta: dict[str, float]
    status: str
```

## Complexity
LNS reduces to ~10-20% of variables; typical 2-10 s.

## Explainability
Diff narrative: who replaced whom, why, KPI change, what was sacrificed.

## Failure modes
No feasible repair -> list missions to drop in priority order; fall back to full mode; RL agent as alternative (stretch).

## Validation
Scripted scenario events with expected outcomes; compare fast LNS vs full solve gap; measure disruption count.
