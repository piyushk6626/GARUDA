# Rolling-horizon retasking implementation

The public entry point remains `async retask_rolling_horizon(payload, ctx)`.
`schemas.py`, `Event`, `PlanDraft`, `PlanDiff`, provider protocols and `SPEC`
retain their existing fields, defaults and signatures. Only this package and
its focused test file implement this tool; `sim.rl_retask` is an independent
alternative and remains outside this implementation.

## Runtime

Install the already-declared `garuda[opt]` extra. The solver prefers HiGHS with
explicit MIP-start hints, then available CBC. It supports PuLP 2/3 and 4 APIs.
The caller supplies implementations of the existing synchronous `ScenarioSource`,
`PlanStore` and provider ports. Their foundation implementations, the shared
assignment solver, registry, executor and independent project validator are
currently stubs. This package owns a local assignment MILP and adds frozen and
stability extensions through the frozen `AssignmentProblem` hook. It does not
implement or modify those other tools or infrastructure components.

Gathering and solving run off the async event loop with `asyncio.to_thread` until
the shared process executor is implemented. The optional LangGraph adapter uses
the same implementation and public JSON payload/output. Supply the context as
`config={"configurable": {"ctx": ctx}}`.

## Event parameters

`Event.params` remains the project's unrestricted dictionary; no fields are added
to its schema. The implementation consumes these conventions:

| Event | Entity / parameters |
| --- | --- |
| `aircraft_nmc` | Existing tail in `entity_id`; optional ISO datetime `etr`, `ETR`, `maint_return_eta`, `end_time`, or `until`. Without an end it remains unavailable. |
| `base_closed` | Existing base in `entity_id`; optional end datetime as above. Replacements launch from open bases using aircraft stationed there. |
| `p1_request` | Existing mission in `entity_id`, or full existing `Mission` shape in `params.mission`. If both are supplied their IDs must match. Priority becomes 1 and value is at least 100, matching the synthetic priority scale. |
| `new_sam` | Existing threat in `entity_id`, or full existing `Threat` shape in `params.threat`. If both are supplied their IDs must match. |
| `tanker_lost` | Existing AAR-capable tail in `entity_id`; receiver AAR requirements are preserved during rescheduling. |
| `weather_override` | Existing base/mission in `entity_id`, or explicit mission list; `params.p_go` or `params.flyability` in [0, 1], with optional end datetime. |

All events may supply `params.mission_ids` as a list of explicit affected IDs.
Event changes are local overlays; neither the scenario snapshot nor the parent
plan is mutated. New mission/threat records supplied inline must also be published
by the data layer before other tools, or subsequent calls, can resolve them from
the scenario source. The frozen `PlanDraft` has no field for persisting those records.

## Behaviour

The event time anchors the freeze window. Already airborne/executing sorties and
existing departures at or before the freeze boundary lock their entire mission
package, including exact aircraft, configuration and timestamps. Completed
assignments are retained and recent recovery reserves turnaround time. An event
that makes a frozen package locally infeasible returns `INFEASIBLE`, the parent
plan ID, an empty diff, zero KPI deltas and the affected missions. It writes no
child draft. Frozen resource conflicts detected by the MILP behave similarly.

Fast mode frees affected missions, competing aircraft/base resources and linked
dependencies. All other assignments, including their absence, are fixed. A failed
repair or dropped mission triggers a full pass under the remaining solve budget.
Full mode considers outstanding requests inside a 72-hour horizon (the assignment
tool's existing default) and always retains freeze constraints. TOT candidates use
hourly points, window endpoints and exact previous timestamps. An `OPTIMAL` result
is optimal for this discretised model; a restricted optimum reports `FEASIBLE`.
Timed-out incumbents report `FEASIBLE_NOT_OPTIMAL`; seeded or fractional values
without a valid incumbent are never extracted. A bounded, independently checked
greedy fallback can supply a feasible draft when no incumbent is returned. Its
emergency search has a separate maximum one-second budget.

The model enforces package sizes and a shared TOT, dependencies, aircraft
occupancy through recovery/turnaround, cumulative inspection margin, weapon stock
minus reservations, hourly base capacity, eligible crew staffing/occupancy/duty,
and coupling to overlapping surviving tanker sorties with aggregate offload fuel.
It uses the supplied route, availability, weather, capacity, Pk and component-risk
providers. Staffing and qualification conventions come from the existing
synthetic crew catalog and query rules. Availability/weather probabilities below
0.5 prohibit a candidate; effect/risk weights retain the assignment doc's 0.3/0.2
defaults. Preemption costs 10% of the previous mission's value; stability applies
the caller's weight to the assignment symmetric difference.

SAM exposure is a conservative horizontal segment/envelope check over the local
threat overlay; it includes threats appearing after an airborne departure. The
raster provider has no georeferencing/update interface. Detailed raster routing,
altitude deconfliction and tanker VRP scheduling remain responsibilities of their
separate tools. Tanker feasibility here checks time and aggregate offload, not
AARP placement, transit, receiver service durations or a routed tanker schedule.
Support configuration IDs absent from `LoadConfig` can be reused from the parent
plan for AAR/AIRLIFT/SAR; no invented support configuration IDs are introduced.
Aircraft cannot be relocated by this tool because `Assignment` has no launch-base
field and `ScenarioSource` is read-only.

Successful repairs persist a new `DRAFT` with `parent_plan_id`, assignment diff,
degraded missions and KPI deltas relative to the parent's stored KPIs.
Risk updates replace the active assignments' contribution while preserving the
completed sorties' contribution in the parent's aggregate KPI.
Changed assignments lose their stale legs, sortie slots and crew assignments; unchanged
records are retained. Exposed routes are invalidated and listed as degraded for
downstream replanning. Crew schedules for changed sorties must be materialised by
the dedicated scheduling tool. The retasking tool never commits a plan.

Run `python -m pytest tests/test_retask_rolling_horizon.py` from `backend`.
Tests exercise all event types, freeze boundaries, exact diffs, draft isolation,
resource and dependency feasibility, stability/preemption, AAR loss, solver status
handling, independent greedy comparison and the optional graph adapter.
