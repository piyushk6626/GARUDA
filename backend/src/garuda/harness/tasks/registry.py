"""One task definition per optimizer tool."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from garuda.contracts.tool import ToolSpec
from garuda.harness.tasks.prompts.assign_missions import prompt as assign_missions_prompt
from garuda.harness.tasks.prompts.assign_weapons_to_targets import prompt as assign_weapons_prompt
from garuda.harness.tasks.prompts.compute_sortie_capacity import prompt as sortie_capacity_prompt
from garuda.harness.tasks.prompts.deconflict_airspace import prompt as deconflict_prompt
from garuda.harness.tasks.prompts.pareto_tradeoffs import prompt as pareto_prompt
from garuda.harness.tasks.prompts.plan_route import prompt as plan_route_prompt
from garuda.harness.tasks.prompts.retask_rolling_horizon import prompt as retask_prompt
from garuda.harness.tasks.prompts.schedule_crew import prompt as schedule_crew_prompt
from garuda.harness.tasks.prompts.schedule_tanker_aar import prompt as tanker_prompt
from garuda.optimization.assign_missions.tool import SPEC as ASSIGN_MISSIONS_SPEC
from garuda.optimization.assign_missions.tool import assign_missions
from garuda.optimization.assign_weapons_to_targets.tool import SPEC as ASSIGN_WEAPONS_SPEC
from garuda.optimization.assign_weapons_to_targets.tool import assign_weapons_to_targets
from garuda.optimization.compute_sortie_capacity.tool import SPEC as SORTIE_CAPACITY_SPEC
from garuda.optimization.compute_sortie_capacity.tool import compute_sortie_capacity
from garuda.optimization.deconflict_airspace.tool import SPEC as DECONFLICT_SPEC
from garuda.optimization.deconflict_airspace.tool import deconflict_airspace
from garuda.optimization.pareto_tradeoffs.tool import SPEC as PARETO_SPEC
from garuda.optimization.pareto_tradeoffs.tool import pareto_tradeoffs
from garuda.optimization.plan_route.tool import SPEC as PLAN_ROUTE_SPEC
from garuda.optimization.plan_route.tool import plan_route
from garuda.optimization.retask_rolling_horizon.tool import SPEC as RETASK_SPEC
from garuda.optimization.retask_rolling_horizon.tool import retask_rolling_horizon
from garuda.optimization.schedule_crew.tool import SPEC as SCHEDULE_CREW_SPEC
from garuda.optimization.schedule_crew.tool import schedule_crew
from garuda.optimization.schedule_tanker_aar.tool import SPEC as TANKER_SPEC
from garuda.optimization.schedule_tanker_aar.tool import schedule_tanker_aar

ToolFn = Callable[..., Awaitable[BaseModel]]
PromptFn = Callable[[datetime], str]


@dataclass(frozen=True)
class TaskDefinition:
    spec: ToolSpec
    fn: ToolFn
    prompt: PromptFn
    tool_budget: int = 8


def _task(spec: ToolSpec, fn: ToolFn, prompt: PromptFn) -> tuple[str, TaskDefinition]:
    return spec.name, TaskDefinition(spec=spec, fn=fn, prompt=prompt)


TASKS: dict[str, TaskDefinition] = dict(
    (
        _task(ASSIGN_MISSIONS_SPEC, assign_missions, assign_missions_prompt),
        _task(SCHEDULE_CREW_SPEC, schedule_crew, schedule_crew_prompt),
        _task(ASSIGN_WEAPONS_SPEC, assign_weapons_to_targets, assign_weapons_prompt),
        _task(SORTIE_CAPACITY_SPEC, compute_sortie_capacity, sortie_capacity_prompt),
        _task(PLAN_ROUTE_SPEC, plan_route, plan_route_prompt),
        _task(TANKER_SPEC, schedule_tanker_aar, tanker_prompt),
        _task(DECONFLICT_SPEC, deconflict_airspace, deconflict_prompt),
        _task(RETASK_SPEC, retask_rolling_horizon, retask_prompt),
        _task(PARETO_SPEC, pareto_tradeoffs, pareto_prompt),
    )
)
