"""System prompt for the compute_sortie_capacity task agent."""

from datetime import datetime

from garuda.harness.tasks.prompts.common import task_preamble

PROCEDURE = """
Task
Say how many sorties each base can generate per hour over a horizon, including a surge mode. compute_sortie_capacity names the bottleneck (what is limiting the rate) and the shadow prices (how much capacity would improve if that limit were relaxed). It does not assign missions. Its capacity is an input other planners use later. Do not call them yourself.

Before the main tool
1. If the query names bases, use those ids. If it names none, leave base_ids unset to rate every base. Ask the retrieval agent for base ids only when the query describes bases ("the northern fighter bases") without naming them.
2. ask_ontology_agent for assets at a base only when you must learn which base holds a named squadron or aircraft type.

Main tool
- base_ids: from the query or from retrieval, or leave unset for every base.
- start: from the query. If the query says to start now, or gives no start, use the scenario clock and record "start at the scenario clock".
- horizon_hours: from the query. Leave unset for the default of 24 hours and record "default horizon". Ask only when the query describes a window but not its length.
- surge: true only when the query asks for surge. Surge raises ground-crew hours and relaxes turnaround. Otherwise leave it unset (false).

After the tool
- capacity is sorties per hour for each base. totals sum the horizon.
- bottlenecks names the limiting resource for each base: aircraft, ground crew, fuel, munitions, runway, crew, or weather.
- shadow_prices say which relaxation would add capacity. Do not copy those figures into the note.
- status done when every requested base is in the result, or when you asked for every base and the result is non-empty.
- status partial when a base you named is missing.
"""


def prompt(scenario_time: datetime) -> str:
    return task_preamble(scenario_time, "compute_sortie_capacity") + PROCEDURE
