# Crew fatigue assessment

Call `await assess_crew_fatigue(payload, ctx)` with the existing input, output,
and context contracts. No schema, dependency, provider, registry, or other tool
is changed. The callable reads the scenario snapshot and optional plan through
the existing ports. CPU work runs in a worker thread. The legacy `STUB_OUTPUT`
remains available to the separate graph scaffold; the callable never returns it.

`crew_ids=None` and `base_ids=None` select the full roster. Empty lists select
nobody; both filters intersect. Unknown IDs raise `ToolInputError`. Items and
base summaries are sorted deterministically. Summary counts cover selected crew
and include all three risk bands for each represented base.

`at_time` defaults to `ctx.scenario_time`. All timestamps must be aware and are
compared in UTC. The latest eligible crew observation is used: the roster wins
ties with its duty log. Future observations cannot inform historical queries.
Queries before all available observations fail. A newer duty log supplies duty
and fatigue; off-duty logs lack a rest duration, so their timestamp starts an
assumed rest opportunity. They are snapshots, not exact duty intervals.

The model reconstructs 24 hours from duty/rest counters, beginning with a
normalized reservoir of 0.8. It permits up to 6.5 hours of sleep at the beginning
of a rest block, repeated every 24 hours of uninterrupted rest. Awake reservoir
drain is 0.02/hour and sleep recovery is `1 - (1 - R) * 0.9**hours`. Circadian
amplitude is 0.05 with a 17:00 Asia/Kolkata peak, following roster generator
defaults; bases have no timezone field. Time on duty costs 0.006/hour. Sleep
inertia costs 0.03 immediately after waking, decaying with a 0.5-hour time
constant. These parameters are approximate prototype assumptions. A worse
observed fatigue score conservatively lowers the inferred initial reservoir;
recovery requires subsequent rest. Sleep windows and scoring use exact interval
edges and samples no farther than 15 minutes apart. Projected peaks are sampled
estimates, not continuous-time optimization results.

`now` is the baseline assessment at the requested time. Active duty continues
until an explicit planned release; its actual release is otherwise unknown.
Plans are joined by `crew_assignments[].slot_id` to `sortie_slots`, preserving
fractional durations. Only assigned crew get a projection. Plan simulation
starts at the same eligible observation, replaying assigned slots through the
last remaining sortie. Scores before `at_time` are excluded from the peak.
The peak includes `now`, so `projected_peak >= now`. With no remaining assigned
sorties it equals `now`. Consecutive slots preserve continuous time on duty;
gaps permit assumed sleep and recovery. Invalid slot references, duplicate
assignments/slot IDs, reversed intervals, and overlapping slots for one crew
raise `ToolInputError`. Night flags are reported as drivers; there is no invented
night-sortie coefficient beyond the circadian calculation.

Bands use projected peak: GREEN < 0.4, AMBER 0.4 through 0.7 inclusive, RED > 0.7.
Drivers identify inferred sleep history, current duty, prolonged wakefulness,
circadian lows, sleep inertia, planned sorties, and supplied night flags.
Seven-day flight hours, detailed sleep windows, historical night sorties, and
timezone shifts are absent from the frozen scenario contract and are not
fabricated. Optional ML residual calibration is omitted.

The tool retains `side_effects="none"`: it does not persist `fatigue_score` or
modify crew, plans, providers, or models. The design note's future persistence
use requires a separate writer; the scenario port has no write operation.
This model supports prototype demonstrations and is not a certified aeromedical
fitness assessment.

Verification: `python -m pytest tests/test_assess_crew_fatigue.py -q` in `backend`.
