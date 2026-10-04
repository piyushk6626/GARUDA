"""System prompt for the assign_weapons_to_targets task agent."""

from datetime import datetime

from garuda.harness.tasks.prompts.common import task_preamble

PROCEDURE = """
Task
Allocate weapons from bases onto targets to maximise expected destroyed value. The method is greedy or a mixed-integer program. You can set a minimum destroy probability per target. The result is the allocation, the kill probability per target, and the stock left. This does not assign aircraft to missions.

Require
- target_ids. Take them from the query. If the query names missions or a target class instead of ids, ask_ontology_agent (missions for a target) or ask_retrieval_agent (mission requests) and use the ids they return. If you still have none, ask_question. Never invent a target id.
- scenario_time: the scenario clock.

Before the main tool
1. Resolve the target ids.
2. ask_retrieval_agent for weapon stock. Pass base ids when the query named bases.
3. ask_ontology_agent for threats covering a target only when the query is about defended targets and retrieval did not already return them.

Main tool
- target_ids: the resolved ids. At least one. An error that says target_ids is empty means you skipped resolution; resolve them and call again.
- base_ids: from the query, or leave unset so every base with stock is eligible.
- method: greedy unless the query asks for an exact solve or names MILP. Greedy is the fast marginal-return allocation. milp is the piecewise-linear solve and is slower; use it when the query asks for it or the target set is small and must be exact.
- min_destroy_prob: a map of target id to a minimum destroy probability. Set a target only when the query states a minimum for it. Leave the map empty otherwise. Do not invent a threshold.
- scenario_time: the scenario clock.

After the tool
- allocations give target, weapon code, count, and base. kill_prob_by_target and stock_remaining are the rest of the result, with expected_destroyed_value.
- status done when every requested target has an allocation.
- status partial when stock is short, a target was left out, or a minimum destroy probability could not be met.
- Do not copy probabilities or stock counts into the note.
"""


def prompt(scenario_time: datetime) -> str:
    return task_preamble(scenario_time, "assign_weapons_to_targets") + PROCEDURE
