# RL retasking tool

`async rl_retask(payload: RlRetaskInput, ctx: ToolContext) -> RlRetaskOutput`
implements advisory inference. The input/output schemas, defaults, `Event`,
`PlanDraft`, `PlanDiff`, provider contracts and `SPEC` are unchanged. Changes are
confined to this package and its tests. The tool never commits or modifies its
parent plan or scenario snapshot.

## Runtime and checkpoints

The caller supplies the existing scenario, plan, provider and model ports.
Load a trained `PolicyArtifact` (or a dictionary with its fields) through
`ctx.models.load("rl_retask")`. For example:

```python
from sb3_contrib import MaskablePPO
from garuda.sim.rl_retask.model import PolicyArtifact

artifact = PolicyArtifact(
    model=MaskablePPO.load("checkpoint.zip", device="cpu"),
    version="ppo-2026-10-05-v1",
    max_aircraft=64,
    max_missions=64,
    max_bases=16,
)
ctx.models.save("rl_retask", artifact, {"version": artifact.version})
```

Inference uses the documented `predict(observation, deterministic=...,
action_masks=...)` interface of
[MaskablePPO](https://sb3-contrib.readthedocs.io/en/master/modules/ppo_mask.html).
The checkpoint must have been trained for the adapter described below; arbitrary
SB3 checkpoints are not interchangeable. Its action/observation spaces, adapter
version, padding bounds and capability vocabulary are checked before use. No
checkpoint is trained, downloaded or fabricated by a tool call. A missing,
incompatible, failing or invalid policy uses the validated MILP fallback and
reports `milp-fallback` (or `<policy-version>/milp-fallback`).

The existing `garuda[opt]` extra provides the HiGHS/CBC comparator and fallback.
`garuda[rl]` is needed when loading an SB3 checkpoint; injected compatible policies
need only the base NumPy dependency. Shared context implementations, executor,
registry, providers and simulator signatures are left to their respective owners.

## Versioned inference adapter

`GarudaRetaskEnv` is a bounded decision adapter for an event, not the 72-hour
stochastic Gymnasium training simulator. Training, curriculum, behaviour cloning,
stochastic transitions and policy-quality evaluation are outside this tool's
scope. There is no repository checkpoint, so default calls currently use MILP.

The `garuda-retask-v1` observation is a flat `float32` vector, concatenating
row-major padded blocks. Real rows start with a presence flag; padded rows are
zero. Aircraft, active missions and bases are sorted by their identifiers.
Capability columns use the ordered `PolicyArtifact.capabilities` vocabulary.

| Block | Shape | Columns |
| --- | --- | --- |
| Aircraft | `A x (8+B+C)` | Presence; FMC/PMC/NMC/AOG/IN_MISSION flags (busy tails shown in mission); fuel fraction; maximum candidate risk; base one-hot; capabilities |
| Missions | `M x (9+C)` | Presence; priority/5; value/100; window-start and end offsets/72h; min and max aircraft/A; selected count/max aircraft; maximum candidate risk; required capabilities |
| Bases | `B x 2` | Presence; minimum candidate-hour capacity/100 |
| Pairs | `A x M x 4` | Currently feasible; chosen candidate risk; Pk; matches prior assignment |

`A`, `M`, `B` are checkpoint padding bounds; `C` is its vocabulary size.
Action 0 means skip/finish. Action `1 + aircraft_index*M + mission_index`
selects that pair. A deterministic candidate resolver chooses a feasible
configuration/TOT, preferring prior assignments, lower risk, higher Pk, earlier
TOT, then configuration ID. Masks change after every selection and reject padded
pairs, failed aircraft, unavailable resources, already selected tails in the same
package, frozen missions and unmet dependencies. Partial packages may accumulate
tails, but the final independent checker enforces their actual minimum size.
Skipping an incomplete package or ignoring a mask replaces the policy result
with MILP. `deterministic` is forwarded to the loaded policy without reseeding
its global RNG; stochastic checkpoint RNG ownership belongs to its producer.

## Validation, comparison and plans

This package reuses the existing rolling-horizon problem builder, independent
selection checker, MILP backend and KPI/diff helpers, without invoking another
public tool. Both decisions see one snapshot and identical event overlays: a
72-hour horizon, 2-hour frozen window, stability weight 5, full MILP mode with a
10-second solve budget. Event parameters follow the existing
[retasking conventions](../../optimization/retask_rolling_horizon/README.md).
The policy has at most four seconds for masks/decisions after gathering, with a
six-second budget from the start of gathering. Port reads and individual model
calls cannot be forcibly interrupted; the framework's tool timeout remains 20s.

Every usable result passes the independent event-aware selection checker for
packages/TOT, dependencies, freeze, aircraft occupancy/inspection, crew, weapon
stock, base capacity and tanker fuel. The shared `validate_plan` is also called;
while it raises its foundation `NotImplementedError`, local validation remains
mandatory. Other shared-checker violations reject the result and trigger fallback.
Detailed routed airspace/AAR checks remain responsibilities of those separate
tools, as in the existing retasking backend. Frozen conflicts or absence of any
validated result raise `ToolInputError` and persist nothing, since the fixed output
has no feasibility-status field.

A feasible RL result remains advisory even when MILP has a higher value. Both are
independently checked; only the chosen result is stored as a new `DRAFT` with a
parent ID, exact diff and updated KPIs. Changed assignments lose stale flight
legs, sortie slots and crew schedules. Unchanged records and completed sorties
are retained; SAM-exposed routes are invalidated for downstream replanning.
Outstanding parent unassigned missions are retained outside the active horizon.

`compare_with_milp` has these numeric entries when MILP yields a validated result:

- `value_gap`: chosen plan value minus MILP value.
- `objective_gap`: chosen stability/preemption-adjusted objective minus MILP.
- `rl_latency_ms`, `milp_latency_ms`: measured phase latencies.
- `used_milp`: 1 for fallback, 0 for RL.
- `constraint_violations`: 0 after independent selection validation.

If comparison is unavailable but RL is independently feasible, it is `None`.
`decision_latency_ms` measures the complete call including comparison and storage.
Computation runs off the event loop; persistence happens only after the worker
returns, so a cancelled call cannot create a draft from its background worker.
`SPEC.side_effects="none"` is preserved as the project's advisory classification;
the returned plan ID references a stored advisory draft, never a committed plan.
The optional LangGraph adapter uses the same tool with
`config={"configurable": {"ctx": ctx}}`.

Run `python -m pytest tests/test_rl_retask.py tests/test_retask_rolling_horizon.py`
from `backend`. The optional SB3 test checks a real saved/loaded policy's space
compatibility; it does not establish trained policy quality.
