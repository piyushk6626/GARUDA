# Mission assignment implementation

Call `await assign_missions(payload, ctx)` with the existing input schema and
`ToolContext`. Install the already-declared backend `opt` extra for PuLP with
HiGHS or CBC. Both PuLP 3 and 4 are supported. The public input/output schemas,
contracts, provider protocols and frozen `problem.py` are unchanged.

The package reads `ctx.scenario.snapshot()` once, uses the supplied providers,
builds a sparse MILP, independently validates the incumbent, and saves a
`PlanDraft` with seat-level sortie slots through `ctx.plans.put`. It does not mutate the scenario or reserve
stock. `INFEASIBLE` returns no assignments and `plan_id=""`, and saves no draft.
Optional unassignable tasks yield a valid empty plan, not an infeasible model.
A timeout returns a validated incumbent or greedy fallback as `FEASIBLE`.
If neither satisfies the locks, it raises `ToolInputError` requesting more solve
time rather than asserting infeasibility without proof.

`preprocess.PreparedProblem` subclasses the frozen `AssignmentProblem` and
preserves its locks and `(model, vars, problem)` extension hook. Its private
`build_problem` supplies the parameters missing from the frozen skeleton.
Other tool packages and shared infrastructure are not implemented here. The
registry, envelope, provider defaults, executor and global validator are still
stubs in this checkout. A background thread and `validate.py` keep this tool
usable with a supplied context without changing those shared components.

Current modelling choices within the available data:

- One departure option per six-hour bucket, moved to the earliest feasible
  mission arrival; exact locked departures and TOTs are retained.
- `mission_ids=None` selects REQUESTED/PLANNED tasks; `[]` selects none. Only
  selected pending tasks are optimized. A prerequisite outside the selection
  is satisfied only if its scenario status is DONE.
- Mission value is `Mission.value` (the generator already encodes priority
  points). Effectiveness and component risk use the input weights. KPIs report
  fulfilled value, fulfilled mission fraction in [0,1], sortie count and summed
  risk. No public gap, sensitivity or shadow-price fields are added.
- Forecast availability and weather GO must be at least 0.5; configuration Pk
  must meet `Mission.min_pk`. Loaded station compatibility and every resource
  quantity are checked, including reserved stock. Payload range penalties and
  route distance determine unrefuelled round-trip range. Recovery and task
  duration count toward aircraft occupancy and cumulative inspection margin.
- Capacity providers are hourly; their capacities, capped by the base's hourly
  maximum, are integrated into six-hour buckets (including a partial final
  bucket). Capacity is constrained per bucket, as in the specification.
- Crew must be at the launch base, medically fit, qualified for the mission and
  type, current, rested and below the fatigue threshold. Su-30MKI requires a
  pilot and squadron-matched WSO. Cross-qualified crew cannot be double counted.
  The assignment-stage policy conservatively requires 12h rest after each
  sortie and at most 14 flying hours within any rolling 24h start-time window;
  `schedule_crew` remains responsible for outputting crew assignments.
- Normal dependencies require prerequisite completion before dependent TOT.
  AAR, AIRLIFT and SAR have no supported `LoadConfig.role`; as agreed, they are
  reported unassigned. No synthetic config IDs or assumed tanker fuel are
  invented. A mission needing an unsupported AAR prerequisite is unassigned.

Unassigned reasons use only the existing `ReasonCode` literals. Binding stock,
base, aircraft, crew and support constraints use `binding_constraints`. These
are tight constraints at the returned incumbent, not LP shadow prices.

Run `python -m pytest tests/test_assign_missions.py -q` from `backend` for
known-optimum, baseline, validator, lock, resource, dependency and timeout checks.
