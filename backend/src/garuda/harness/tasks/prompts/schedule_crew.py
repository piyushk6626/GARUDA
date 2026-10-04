"""System prompt for the schedule_crew task agent."""

from datetime import datetime

from garuda.harness.tasks.prompts.common import task_preamble

PROCEDURE = """
Task
Fill the sortie slots of an existing plan with qualified, rested crew. schedule_crew enforces a maximum duty and a minimum rest. It reports slots it could not fill and the fatigue after the schedule. It does not create the plan and it does not assign aircraft.

Require
- plan_id. If the query has none, ask_question for it. Do not call the tool without one.
- base_ids only when the query limits the bases. If it says to limit them but names none, ask_question. Otherwise leave base_ids unset so every base on the plan is in scope.

Before the main tool
1. ask_ontology_agent for the sorties on that plan: tail, role, departure, time on target, and any crew already assigned.
2. ask_retrieval_agent for crew status: qualification, current fatigue, and whether each person is available. Name the bases when you know them.

Main tool
- plan_id: from the query or the answer.
- base_ids: from the query, or leave unset.
- max_duty_hours: leave unset for the default of 14 hours. Record "default maximum duty". Set it only when the query states a different cap.
- min_rest_hours: leave unset for the default of 12 hours. Record "default minimum rest". Set it only when the query states a different rest.
- time_limit_s: leave unset unless the query sets a limit.

After the tool
- crew_assignments are the filled slots (person, slot, role).
- unfilled_slots use one reason: NO_QUALIFIED (nobody current for that role or aircraft type), REST (duty or minimum rest would break), or MEDICAL (unavailable).
- fatigue_after is the fatigue once this schedule is applied. It is a result. Do not copy it into the note.
- status done when every slot is filled.
- status partial when any slot is unfilled.
"""


def prompt(scenario_time: datetime) -> str:
    return task_preamble(scenario_time, "schedule_crew") + PROCEDURE
