# Component RUL tool

`predict_component_rul(payload, ctx)` preserves the existing input/output models,
async signature, SPEC, timeout, and read-only side effects. Only this tool package
is implemented; training, model persistence, graph/registry/provider wiring and
other tools remain separate workstreams.

The tool reads one scenario snapshot and optionally loads
`ctx.models.load("predict_component_rul")`. No scenario, plans, or models are
written. Inference runs in a worker thread while the shared executor is a stub.

## Existing data and baseline

The scenario has no component log or overhaul dates. Each aircraft is represented
by engine, radar, hydraulics, and avionics, each with the same **age proxy**:
`inspection_cycle_hours - hours_to_next_inspection`. The existing output field
`hours_since_overhaul` contains this proxy, not a measured component overhaul age.
Individual engines and absent/uninstalled components cannot be distinguished by
the current scenario. Aircraft status does not supply a component failure label.

An unavailable model (None, KeyError, FileNotFoundError, NotImplementedError)
uses the document's synthetic Weibull parameters: shape 2.2, scale 800 flight
hours. The document specifies these only for engines; the same parameters are
used for all four types as an explicitly uncalibrated baseline, without inventing
component-specific rates. Baseline use is logged. These estimates need component
records and calibration before they can represent operational reliability.

## Calculations and filtering

- Conditional next-h failure: `1 - exp(-(H(t+h) - H(t)))`, with
  `H(t) = (t / scale) ** shape`.
- RUL p10/p50/p90 are conditional failure-time percentiles in **remaining flight
  hours**. They solve `H(t+r) - H(t) = -log(1-p)` for p=0.1/0.5/0.9.
  They are not model-parameter confidence intervals.
- Aircraft risk is `1 - product(1 - p_component)` over the **returned** component
  types, assuming independence. A component filter therefore changes this risk.
- Inspect a component if its next-h failure probability is at least 10%, or if
  the aircraft inspection margin is exhausted at or before the horizon. The 10%
  threshold is a tool policy, not a threshold specified by the survival document.
  No replacement or maintenance booking is performed.
- None filters mean all; empty lists match nothing; duplicates are removed.
  Unknown tail numbers match nothing, while unknown component types raise
  `ToolInputError`. Results are sorted by tail, then the component order above.
- Zero hours is accepted and produces zero next-h risk. Negative/nonfinite
  horizons raise `ToolInputError` without modifying the schema. Nonfinite or
  inconsistent inspection hours also raise `ToolInputError`.

Hazard arithmetic avoids survival underflow and preserves small probabilities
when aggregating aircraft risk. Component age and horizon are flight hours;
the tool does not advance ages with wall-clock time or use future history.

## Supplying per-component Weibull parameters

The existing ModelStore can supply the internal artifact below. Missing or
invalid parameters for a requested component raise an error; a corrupt model
does not silently fall back. Both values must be finite and positive.

```python
from garuda.ml.predict_component_rul.predict import (
    ComponentRulArtifact, WeibullParameters,
)

artifact = ComponentRulArtifact(parameters={
    "engine": WeibullParameters(shape=2.2, scale_hours=800.0),
    # Supply fitted parameters for every other requested component type.
})
```

Parameters may be fitted externally on right-censored component records.
The tool does not fit parameters, fabricate training events, implement Cox PH,
or infer unrecorded component-level covariates. The inspection-age proxy still
applies when an artifact is supplied.
