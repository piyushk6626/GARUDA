"""Event-aware input preparation using the existing retasking constraint model."""

from dataclasses import dataclass, replace
from datetime import datetime
from types import SimpleNamespace

from garuda.common import as_utc
from garuda.optimization.retask_rolling_horizon.problem import (
    RetaskProblem,
    build_problem as build_retask_problem,
)
from garuda.schemas.mission import GeneratedScenario


@dataclass
class Problem:
    retask: RetaskProblem
    scenario: GeneratedScenario
    now: datetime
    seed: int
    deterministic: bool


def build_problem(payload, ctx) -> Problem:
    # Both policies see the same snapshot, event overlay, horizon, frozen window
    # and stability cost. No call to another public tool or shared contract edit.
    scenario = ctx.scenario.snapshot()
    snapshot_ctx = replace(ctx, scenario=SimpleNamespace(snapshot=lambda: scenario))
    knobs = SimpleNamespace(
        plan_id=payload.plan_id,
        event=payload.event,
        freeze_hours=2.0,
        stability_weight=5.0,
        mode="full",
        time_limit_s=10,
    )
    retask = build_retask_problem(knobs, snapshot_ctx)
    return Problem(
        retask, scenario, as_utc(payload.event.time), ctx.seed, payload.deterministic
    )
