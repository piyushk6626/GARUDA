# Availability tool inference

`forecast_availability(payload, ctx)` retains the existing schemas, signature,
`SPEC`, and context ports. It reads the scenario once and optionally reads the
referenced plan. It does not write plans, models, or scenario data.

`per_aircraft` includes every matching aircraft, sorted by tail, with string
horizon keys. `None` filters mean unrestricted; empty filter lists match nothing.
Positive integer horizons are accepted, preserving their requested order and
deduplicating repeated horizons. Invalid horizons and plan references raise
`ToolInputError` without changing the input schema.

`fleet` keys are `base_id/type`. Each entry summarizes **the first requested
horizon** (24 h by default), as confirmed by the user. Its mean is the expected
FMC **count**, and p10/p90 are exact Poisson-binomial count quantiles assuming
independent aircraft.

## Trained model integration

The existing `ctx.models.load("forecast_availability")` port should return a
`ForecastAvailabilityArtifact` from `predict.py`. It holds:

- `model_version`: the stored model version reported in the tool output.
- `models`: integer horizon to fitted binary classifier or preprocessing pipeline.
  Class 1 means FMC. Models accept pandas frames and expose `predict_proba`.
- `feature_names`: ordered columns from `features.FEATURE_NAMES`. A fitted
  preprocessing pipeline must handle the categorical and missing values.
- `calibrators`: optional horizon-to-isotonic-regressor mapping. Omit when
  calibration is already part of the classifier's fitted pipeline.
- `explainers`: optional horizon-to-SHAP-explainer mapping. Explain in the input
  feature coordinates. Otherwise the tool attempts `shap.TreeExplainer`.

The model must cover all requested horizons. An unavailable or invalid model
falls back for the entire response to `model_version="rule-baseline-1"`, avoiding
mixed versions. Explanation failures preserve model probabilities and return
explicitly labelled rule drivers. SHAP drivers summarize the first requested
horizon; deterministic overrides appear ahead of model drivers.

Feature history excludes rows after `ctx.scenario_time`. Historical sortie and
failure counts use status transitions. Timed plan slots take precedence over
matching assignments; duplicate crew-role slots count once. Assignment-only
plans use the scenario mission's duration. Planned counts and flight hours are
computed separately for each horizon. Ground-crew load and surge remain missing;
spare availability is known false only for `awaiting_parts`. No training labels
or future status rows are used for inference.

The scenario contract has no future maintenance calendar. Maintenance overrides
use the current aircraft status and return ETA; future status-history rows are
not interpreted as a maintenance schedule.

## Baseline and deterministic constraints

The baseline treats FMC as ready, and PMC/NMC/AOG as unready until an explicit
maintenance return ETA. An aircraft currently in flight needs a timed plan return
to become ready. Completed planned sorties incur 4% degradation each, the
midpoint of the synthetic-data specification's 3–5% range.

For both baseline and trained inference, a horizon during maintenance, a planned
flight or its turnaround, or after exhaustion of the inspection margin receives
probability zero. Maintenance intervals end at their ETA; a completed scheduled
inspection resets the inspection cycle. No unrecorded repairs are assumed.

Training, registry persistence, shared executor/registry implementations, and
LangGraph/provider wiring are outside this tool implementation. The existing
`STUB_OUTPUT` remains solely for compatibility with the separate graph scaffold;
the callable tool never returns it. Inference runs in a worker thread while the
shared CPU executor remains unimplemented.
