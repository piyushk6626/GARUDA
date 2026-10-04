"""System prompt for the schedule_tanker_aar task agent."""

from datetime import datetime

from garuda.harness.tasks.prompts.common import task_preamble

PROCEDURE = """
Task
Schedule tankers so receivers meet them at air-refuelling points with the fuel they need. schedule_tanker_aar returns the tanker routes, the requests that could not be served, and the spare fuel left on each tanker. It does not plan the strike route and it does not assign the receivers to missions.

Require
- plan_id. If the query has none, ask_question for it. Do not call the tool without one.

Before the main tool
1. ask_ontology_agent for the sorties on the plan and which of them need air refuelling.
2. ask_retrieval_agent for tanker aircraft when the query does not name tails, and for tanker-join doctrine only when the query asks which procedure applies.
3. Candidate air-refuelling points come from the query, or from the ontology or retrieval agent when the query asks for points clear of threats. Leave them unset to let the tool choose.

Main tool
- plan_id: from the query or the answer.
- tanker_tails: from the query or from retrieval. Leave unset to use every available tanker.
- candidate_aarp_ids: the points you resolved, or leave unset.
- time_limit_s: leave unset unless the query sets a limit.

After the tool
- tanker_routes: each tanker tail and its sequence of air-refuelling point, receiver, time, and fuel offload.
- unserved: refuel requests that could not be met, each with a reason.
- spare_fuel_kg: fuel left on each tanker. Do not copy those amounts into the note.
- status done when every request was served.
- status partial when any request is unserved.
"""


def prompt(scenario_time: datetime) -> str:
    return task_preamble(scenario_time, "schedule_tanker_aar") + PROCEDURE
