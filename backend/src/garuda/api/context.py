"""Build a ToolContext for the local app from a generated scenario."""

from datetime import datetime
from pathlib import Path

from garuda.context.memory import InMemoryPlanStore, InMemoryScenarioSource, LocalModelStore
from garuda.contracts.tool import ToolContext
from garuda.providers.protocols import Providers
from garuda.schemas.mission import GeneratedScenario


class _Unwired:
    """A provider slot that fails with a clear error when a tool calls it."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, item: str):
        def _missing(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError(f"provider {self._name}.{item} is not wired")

        return _missing


def build_context(
    scenario: GeneratedScenario,
    *,
    scenario_time: datetime,
    seed: int,
    model_dir: Path | str,
) -> ToolContext:
    """Scenario, plans, and models are live. Provider-backed tools fail with their own error."""
    unwired = _Unwired
    return ToolContext(
        scenario=InMemoryScenarioSource(scenario, as_of=scenario_time),
        plans=InMemoryPlanStore(),
        models=LocalModelStore(model_dir),
        providers=Providers(
            availability=unwired("availability"),
            component_risk=unwired("component_risk"),
            weather_go=unwired("weather_go"),
            route=unwired("route"),
            capacity=unwired("capacity"),
            pk=unwired("pk"),
            fatigue=unwired("fatigue"),
            threat_risk=unwired("threat_risk"),
        ),
        scenario_time=scenario_time,
        seed=seed,
    )
