# Pareto trade-offs

`await pareto_tradeoffs(payload, ctx)` uses the existing `ParetoTradeoffsInput`,
`ParetoTradeoffsOutput`, `PlanOption` and `ToolSpec` without changes. Only this
package and its tests are implemented. Install the existing `opt` extra for
PuLP with HiGHS/CBC, and `moo` for `method="nsga2"`.

The tool preserves `side_effects="none"`: it reads the base through PlanStore,
reads one scenario snapshot, and never writes plans, changes the base, reserves
resources or calls another tool. Alternative IDs are deterministic fingerprints
of evaluated transient drafts, **not saved PlanStore entries**. A caller that
needs persistent alternatives must explicitly persist drafts in orchestration;
the package's `explain.draft(problem, point)` builds their existing PlanDraft
shape with crew seats and KPIs. No new persistence contract is introduced.

Preparation and solving run in a background thread because the shared CPU
executor remains a stub. The shared assignment preparation, model and independent
validator supply the feasible decision space. The frozen AssignmentProblem and
its extension hook are unchanged, and existing extensions remain applied.

An alternative optimizes the base plan's assigned and unassigned mission IDs;
an empty base uses all pending missions. The planning window starts at the
base's scenario_time and uses the assignment tool's default 72-hour horizon
and six-hour departure samples, since PlanDraft has no horizon field. Feasible
exact base departures are added alongside these samples and are not locked.
Unsupported/invalid base references raise an actionable ToolInputError.

Objective meanings within the available contracts:

- Value: fulfilled mission value counted once per complete mission package.
- Risk: sum of the shared assignment model's per-sortie component-risk estimates.
- Munitions: loaded expendable weapon counts; reusable PODS are excluded. The
  contracts do not expose partial expenditure during a sortie.
- Fatigue: largest active crew member's initial provider score plus accumulated
  `phi(slot)` increments, capped at 1. Empty plans have zero operational fatigue.
  Crew eligibility, rest, duty, pairing and staffing constraints remain enforced.
- Disruption: assignment additions plus removals vs the base. A changed tail,
  load or time counts as removal and insertion. Crew/route changes are excluded.

Epsilon searches first solve each selected objective alone. Normalized Cartesian
epsilon thresholds are swept for the other varying objectives, with a bounded
covering sample for more than two objectives. A lexicographic AUGMECON second
solve preserves the primary optimum and maximizes normalized slacks. Weighted
search uses a normalized simplex grid; both methods seed adjacent solves.
NSGA-II uses binary assignment and crew genomes, feasible anchor seeds, a
population of 100, and up to 50 generations. Every retained plan passes the
independent assignment validator.

Each scalar optimization job, including its tie-break, is bounded by
`time_limit_s_per_solve`. Repeated search uses a 110-second total solve budget
within the existing 120-second ToolSpec. Valid time-limited incumbents are kept;
infeasible epsilon points and missing incumbents are skipped and noted in the
existing `key_differences` field. If no incumbent is available, ToolInputError
asks for more per-solve time. Bounds describe the sampled non-dominated frontier,
not a proof of the complete frontier or a global nadir.

Dominated and duplicate objective vectors are removed before selecting up to
`n_points` deterministic medoids on normalized objectives. The knee and extremes
are retained when space permits. Labels, assignment differences, marginal
value/risk trade-offs and a knee recommendation use the existing output fields.
No gap, hypervolume or solver-status fields are added to the public output.

Run `python -m pytest tests/test_pareto_tradeoffs.py tests/test_assign_missions.py -q`
from backend. Tests include an enumerated non-convex frontier, optional NSGA-II,
resource/extension feasibility, exact base times, fatigue, reproducibility,
degeneracy, representative selection and unchanged schemas/read-only behavior.
