"""Frozen: AssignmentProblem and the ModelExtension hook used by retask and pareto."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from garuda.contracts.plan import Assignment

VarIndex = dict[tuple[str, str, str, int], Any]  # (tail, config, mission, t) -> LP variable
ModelExtension = Callable[[Any, VarIndex, "AssignmentProblem"], None]  # (LpProblem, vars, problem)


@dataclass
class AssignmentProblem:
    """Pruned ok-tuples plus parameters v, req, avail, cap, stock, pk, risk. TODO(phase1): fields."""

    locked: list[Assignment] = field(default_factory=list)
    extensions: list[ModelExtension] = field(default_factory=list)


def build_problem(payload, ctx) -> AssignmentProblem:
    raise NotImplementedError
