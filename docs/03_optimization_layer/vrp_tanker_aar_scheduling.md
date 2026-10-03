# Tanker / AAR Scheduling (VRP with Time Windows)

Tool: `schedule_tanker_aar`

## Problem
Schedule tankers (IL-78) to meet receivers (strike packages, CAP) at air refuelling points (AARP) within time windows, respecting fuel offload, endurance, and tanker orbit tracks. Equivalent to a VRP with time windows, capacity (fuel), and multiple depots.

## Formulation
**Nodes**: depot (tanker base) per tanker, demand nodes j = refuelling events (receiver, AARP location, time window [a_j, b_j], fuel demand q_j, service time s_j).
**Parameters**: `Q_v` offload capacity of tanker v, endurance `E_v`, speed, travel time `t_{ij}`.
**Variables**: `x_{ijv} ∈ {0,1}` arc used by tanker v; `T_j` start of service; `load` cumulative.

**Objective**: min Σ travel time + μ · unserved demand penalty (drop nodes with penalty) + ν · tanker count used.

**Constraints**: each demand node served at most once (else penalty); flow conservation; capacity Σ q_j y_{jv} ≤ Q_v; time windows a_j ≤ T_j ≤ b_j; T_j + s_j + t_{jk} ≤ T_k; endurance on route ≤ E_v; fuel reserve for tanker's own recovery; tanker avoidance of threat zones (arcs forbidden if risk > r).
Sub-problem: AARP placement chosen from candidate points maximising safety (outside threat range) and minimising receiver detour.

## Library
OR-Tools Routing (`pywrapcp`): time dimension, capacity dimension, disjunctions with penalties. Candidate AARPs as nodes with alternative selection via disjunction groups.

## Pseudocode
```
manager = RoutingIndexManager(n_nodes, n_tankers, depots)
routing = RoutingModel(manager)
add transit callback (travel time), time dimension with windows
add capacity dimension (fuel demand)
add disjunction for each demand with penalty
search = PATH_CHEAPEST_ARC + GUIDED_LOCAL_SEARCH, time_limit
```

## Tool signature
```python
class TankerInput(BaseModel):
    plan_id: str
    tanker_tails: list[str] | None = None
    candidate_aarp_ids: list[str] | None = None
    time_limit_s: int = 15

class TankerOutput(BaseModel):
    tanker_routes: list[TankerRoute]    # tail, sequence of (aarp, receiver, time, offload_kg)
    unserved: list[RefuelEvent]
    spare_fuel_kg: dict[str, float]
    kpis: dict[str, float]
```

## Complexity
Tens of events, 6 tankers: seconds.

## Explainability
Fuel margin per tanker, which receivers lack AAR and impact on missions (feeds back to MILP as constraint `y_aar`).

## Failure modes
Unserved receivers -> suggest alternate routes with shorter legs or delay window.

## Validation
Check fuel/time feasibility independently; compare to greedy nearest-window heuristic.
