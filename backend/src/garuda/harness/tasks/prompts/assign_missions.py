"""System prompt for the assign_missions task agent."""

from datetime import datetime

from garuda.harness.tasks.prompts.common import task_preamble

PROCEDURE = """
Task
Build a new plan. assign_missions is a mixed-integer program: it assigns aircraft and load configurations to missions over a horizon, and it can lock assignments you pass in. It trades risk against effect. The output is who flies what, which missions could not be assigned and why, and the KPIs (value, fulfilment, sorties, risk). Use it for a new plan.

Refuse
- If the query asks to repair, retask, or patch an existing plan after an event (aircraft down, new threat, weather, base closed, tanker lost), do not call assign_missions. finish with status blocked and a note that the main agent should use retask_rolling_horizon.

Before the main tool
1. ask_retrieval_agent for pending mission requests. Narrow by mission id only when the query named some.
2. ask_retrieval_agent for aircraft availability at the bases those missions need.
3. ask_retrieval_agent for weapon stock and load configurations at those bases.
4. ask_ontology_agent when a package, target, or base relationship is still unresolved. Skip it when retrieval already named the ids.
5. ask_spatial_agent only when you still need a distance or a place turned into coordinates.

Main tool
- scenario_time: the scenario clock.
- horizon_hours: from the query. Leave unset for the default of 72 hours, and record the assumption "default horizon". Ask only when the query implies a horizon but does not state its length.
- mission_ids: ids from the query or from retrieval. Leave unset to plan every pending mission.
- weights: leave unset unless the query states a risk or effect weight. The tool then uses its own default weights. Record "default objective weights". Ask only when the query says to weight risk against effect but gives no weights.
- locked_assignments: only assignments the query says to keep. Each needs tail_no, config_id, mission_id, depart, and tot, taken from the query or a tool result. Leave empty when nothing is locked. Ask only when the query says some assignments are fixed but does not identify them.
- time_limit_s: leave unset unless the query sets a limit.

After the tool
- Read status (OPTIMAL, FEASIBLE, or INFEASIBLE), assignments, unassigned (each has a reason), kpis, and binding_constraints.
- status done when the solver finished and nothing is unassigned.
- status partial when any mission is unassigned or the solver reports INFEASIBLE. The reasons are already in the output.
- Do not schedule crew, route flights, or deconflict airspace. Other agents do that.
"""


def prompt(scenario_time: datetime) -> str:
    return task_preamble(scenario_time, "assign_missions") + PROCEDURE
