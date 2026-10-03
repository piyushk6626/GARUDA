# CP-SAT Crew Scheduling

Tool: `schedule_crew`

## Problem
Assign qualified, rested crew to each sortie slot of the plan while obeying duty limits, rest, currency, and minimising fatigue and overuse.

## Formulation
**Sets**: K crew, S sortie slots (aircraft seat slots from the plan) each with start `s_i`, end `e_i`, role `ρ_i`, aircraft type `τ_i`, flags (night, AAR).
**Parameters**: `q_{k,i}` qualified/current for slot i; `f_k` initial fatigue; `D_max` max duty in 24 h (e.g. 14 h); `R_min` min rest (12 h); `H_max` weekly flight hours.
**Variables**: `z_{k,i} ∈ {0,1}`; `d_k` duty hours (int); `ov_k` overload slack.

**Constraints**
1. Fill: Σ_k z_{k,i} = 1 for each slot.
2. Qualification: z_{k,i} ≤ q_{k,i}.
3. No overlap: for slots i, j overlapping or with gap < R_min between duty blocks (given turnaround/brief/debrief buffers), z_{k,i} + z_{k,j} ≤ 1.
4. Rolling-24h duty: Σ_{i in window} dur_i z_{k,i} ≤ D_max (+ slack ov_k penalised).
5. Weekly hours cap.
6. Medical/leave: z_{k,i} = 0 if unavailable.
7. Crew pairing for two-seaters: pilot and WSO of same squadron; optionally same-crew continuity bonus.

**Objective**: min Σ_k (α · fatigue_k(z)) + β · Σ ov_k + γ · imbalance (max−min of flight hours) + δ · pairing breaks.
Fatigue approximated linearly: `fatigue_k = f_k + Σ_i φ_i z_{k,i}` with φ from the fatigue model.

## Library
OR-Tools CP-SAT: boolean vars, `AddExactlyOne`, interval variables with `AddNoOverlap` per crew (optional intervals).

## Pseudocode
```
for slot in slots: create bool z[k,slot] for eligible k
AddExactlyOne(z[:,slot])
for k: optional intervals (start=slot.start-brief, end=slot.end+debrief+rest) -> AddNoOverlap
duty window constraints via linear sums
Minimize(objective); solver.parameters.max_time_in_seconds=T
```

## Tool signature
```python
class ScheduleCrewInput(BaseModel):
    plan_id: str
    base_ids: list[str] | None = None
    max_duty_hours: float = 14
    min_rest_hours: float = 12
    time_limit_s: int = 20

class ScheduleCrewOutput(BaseModel):
    status: str
    crew_assignments: list[CrewAssignment]   # crew_id, sortie slot, role
    unfilled_slots: list[SlotGap]            # reason: NO_QUALIFIED / REST / MEDICAL
    fatigue_after: dict[str, float]
    kpis: dict[str, float]
```

## Complexity
~300 crew x ~150 slots, pruned by qualification: solves in seconds.

## Explainability
For unfilled slots list top blocking rules; show who was nearly eligible (e.g. needs 1.5 h more rest).

## Failure modes
Infeasible -> allow overload slack, flag; propose cross-base crew transfer.

## Validation
Rule checker replays schedule against duty/rest rules; property tests.
