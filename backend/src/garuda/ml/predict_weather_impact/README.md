# Weather impact tool

The async entry point is `predict_weather_impact(payload, ctx)`. The existing
`schemas.py`, shared contracts, tool name, side effects, and timeout are unchanged.
It reads one scenario snapshot and loads `ctx.models.load("predict_weather_impact")`.
Missing models raise `RuntimeError`; incompatible artifacts and invalid model
probabilities raise errors. Inference never trains or writes an artifact.

## Model setup

Install the existing backend `ml` extra to train a calibrated LightGBM model.
The inference module does not import LightGBM or scikit-learn until an artifact
requiring them is loaded by the model store.

```python
from garuda.ml.predict_weather_impact.train import train

artifact = train(ctx)  # deterministic with ctx.seed
ctx.models.save("predict_weather_impact", artifact, artifact.metrics)
```

The artifact contains the estimator, the exact feature order, training-population
climatology, and validation metrics. Estimators must expose `classes_` containing
`GO`, `DEGRADED`, and `NO_GO` and `predict_proba`; inference reorders their columns.
Model-store/context implementations and provider/registry/graph wiring remain
the responsibility of the shared infrastructure workstream.

For a serialized `GeneratedScenario`, offline training is also available from
the backend directory:

```shell
python -m garuda.ml.predict_weather_impact --scenario-json scenario.json --output weather.joblib
```

Load that artifact through the existing model-store port under
`predict_weather_impact`. No artifact is generated automatically during inference.

## Evaluation behaviour

- Resolve bases and aircraft speed from the scenario. A mission supplies its
  target/area and duration; an explicit route supplies its path. Routes must pass
  within 25 km of the mission target/area. Without either, return a base assessment
  flagged `base_only_assessment`.
- Sample the full sortie every 25 km and at waypoints; assign times using cruise
  speed and mission duration. Evaluate the target at arrival and recovery at the
  later of mission duration and transit time. A missing recovery base defaults
  to the launch base. Naive datetimes follow the project's UTC convention.
- Select the latest forecast revision issued by `ctx.scenario_time`, using the
  existing hourly, half-degree grid. Missing cells use artifact climatology and
  add `missing_forecast_climatology` and `low_confidence` to `limiting_factors`.
- Derive base crosswind from wind/gusts and the existing airfield runway heading.
  For unknown ICAO headings, conservatively use total wind and flag the assumption.
- Assess route CB, icing, flyability, and headwind; apply target visibility/cloud
  sensitivity to laser/IR guidance and visual STRIKE/CAS missions.
- Rank up to three improved hourly launch windows fully inside the next 12 hours
  and, when supplied, the mission window. These are hourly forecast candidates.
  Rank improved alternate recoveries while keeping launch and outbound route
  fixed. Return no recommendations using climatology for any part of the sortie.

Training uses forecast features plus synthetic adverse-weather stress cases,
rule-based outcome labels, label noise, and forecast error increasing with lead
time. Sigmoid calibration uses cross-validation within the training partition;
AUC, log loss, Brier score, and calibration error use held-out simulated outcomes.
`backtest_metar(snapshot)` separately reports flight-category forecast accuracy
when matching real Open-Meteo and METAR records are supplied. Synthetic fixtures
cannot establish real operational accuracy.
