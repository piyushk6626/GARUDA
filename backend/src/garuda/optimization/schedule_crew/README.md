# Crew scheduling implementation

Call `await schedule_crew(payload, ctx)` using the existing `ScheduleCrewInput`,
`ScheduleCrewOutput`, and `ToolContext` contracts. Install the already-declared
`opt` dependency extra for OR-Tools. No public contract or dependency declaration
is changed by this implementation.

The tool reads `PlanStore.get(plan_id)` and the scenario crew roster, obtains
fatigue scores and per-slot increments from `FatigueProvider`, and solves a
CP-SAT model. It maximizes filled seats before minimizing fatigue and flight-hour
imbalance. Medical/leave eligibility, role and aircraft qualification, currency,
overlap, minimum rest, rolling-24h duty, and pilot/WSO squadron pairing are hard
constraints. `base_ids=None` considers the full roster; `[]` considers no crew.

The result is replayed against the hard rules before replacing only the draft's
`crew_assignments` through `PlanStore.put`. Scenario crew records are not updated.
An existing draft's crew assignments are replaced, not treated as fixed seats.
Changes to the plan while solving cause a retry error before persistence.

`OPTIMAL` means a complete optimal schedule. `FEASIBLE` means an incumbent was
found before search finished; consult `unfilled_slots` and `coverage_proven`.
`INFEASIBLE` means complete coverage was proved impossible, and the output and
draft contain the best valid partial schedule. `UNKNOWN` means no incumbent was
found and the draft is not changed. No duty overload is silently allowed;
`overload_hours` is zero. Each missing seat retains one of the three existing
reason codes, with details for availability, pairing, or search limits.

The frozen roster has no weekly flight-hours history or weekly cap, and the
specification supplies no brief/debrief buffer values. These limits are therefore
not invented: duty uses the supplied sortie intervals, with minimum rest after
each sortie. Rest before the first sortie is projected from `updated_at` and
`rest_hours_since_last_duty`. Crew already on duty or assigned remain blocked
because their release time is unknown. Same-crew continuity is an optional
specification feature and is not included in the objective.

The shared executor, registry, context implementations, and LangGraph adapter
remain owned by their respective packages. The tool first calls `run_cpu` and
uses a worker thread when that executor raises its current `NotImplementedError`.
The legacy `STUB_OUTPUT` is retained only for compatibility with the pre-existing
graph skeleton; the callable tool never returns it.

Verification: `python -m pytest tests/test_schedule_crew.py -q` from `backend`.
The suite includes an independent exhaustive maximum-coverage oracle and rule
replay for small schedules.
