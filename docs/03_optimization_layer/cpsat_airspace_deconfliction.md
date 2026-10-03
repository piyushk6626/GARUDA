# CP-SAT Airspace Deconfliction

Tool: `deconflict_airspace`

## Problem
Given planned routes and missions, assign time slots and altitude blocks so that no two flights violate separation inside the same zone, zone capacities are respected, and civil traffic conflicts are avoided, while shifting time-on-target as little as possible.

## Formulation
**Entities**: flights f with route segments through zones z; each pass is an interval `[s_{f,z}, e_{f,z}]` where `s_{f,z} = departure_f + offset_{f,z}`.
**Variables**: `δ_f ∈ [−Δ, +Δ]` integer minute shift of departure (or TOT); `h_{f,z} ∈ {levels}` altitude block; interval vars.

**Constraints**
1. Zone capacity: `Cumulative(intervals in z, demands=1, capacity=cap_z)`.
2. Pairwise separation: for flights f,g in same zone with overlapping potential, either time separation `s_g ≥ e_f + sep_t` OR altitude separation `|h_f − h_g| ≥ sep_h` (reified booleans).
3. Zone activation windows: pass interval within `[active_from, active_to]` for restricted-but-activated zones; forbidden outside them.
4. Civil conflict: forbid overlap with predicted civil track intervals (buffer 10 km / 1000 ft / 5 min).
5. Mission constraints: TOT window `[a_m, b_m]` => δ bounded; package members keep relative offsets (formation).

**Objective**: min Σ_f |δ_f| · w_f (priority-weighted) + η · altitude changes.

## Library
OR-Tools CP-SAT: `NewOptionalIntervalVar`, `AddCumulative`, `AddNoOverlap`, boolean reification (`OnlyEnforceIf`), `AddAbsEquality`.

## Pseudocode
```
for f, z in passes: interval[f,z] = NewIntervalVar(start=dep[f]+off, size=dur, end=...)
for z: AddCumulative(intervals_z, ones, cap_z)
for pairs (f,g,z): b = NewBoolVar; time-sep OnlyEnforceIf(b); alt-sep OnlyEnforceIf(b.Not())
Minimize(sum(w_f * abs_shift_f))
```

## Tool signature
```python
class DeconflictInput(BaseModel):
    plan_id: str
    max_shift_min: int = 30
    separation: SeparationRules = SeparationRules(time_min=3, altitude_ft=1000, lateral_km=10)
    include_civil: bool = True

class DeconflictOutput(BaseModel):
    status: str
    adjusted_flights: list[FlightAdjustment]   # flight, shift_min, altitude_block
    remaining_conflicts: list[Conflict]
    total_shift_min: int
```

## Complexity
~100 flights, ~40 zones: pair pruning by spatial index keeps booleans ~ few thousand; seconds.

## Explainability
Lists each shift with the conflict it resolved.

## Failure modes
Infeasible -> report minimal conflict set; suggest reroute via `plan_route` with the zone blocked.

## Validation
Brute-force separation checker on output; replay with ADS-B sample.
