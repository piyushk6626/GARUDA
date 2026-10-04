"""One function per constraint family: rule(plan, ctx) -> list[Violation]. Owners add their own rules."""

from garuda.contracts.plan import PlanDraft
from garuda.contracts.tool import ToolContext
from garuda.validation.plan_validator import Violation


def rule_aircraft_single_sortie(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    raise NotImplementedError


def rule_weapon_stock(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    raise NotImplementedError


def rule_crew_duty_rest(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    raise NotImplementedError


def rule_airspace_separation(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    raise NotImplementedError


def rule_tanker_fuel(plan: PlanDraft, ctx: ToolContext) -> list[Violation]:
    raise NotImplementedError
