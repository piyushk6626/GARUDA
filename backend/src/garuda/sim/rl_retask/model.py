"""Inference of a versioned masked policy; never train during a tool call."""

from dataclasses import dataclass
from math import isfinite
from time import perf_counter

import numpy as np

from garuda.optimization.retask_rolling_horizon.baseline import (
    objective,
    validate_selection,
)
from garuda.optimization.retask_rolling_horizon.model import Solution
from garuda.optimization.retask_rolling_horizon.problem import assignment_key
from garuda.sim.rl_retask.env import CAPABILITIES, OBSERVATION_VERSION, GarudaRetaskEnv

MODEL_NAME = "rl_retask"


@dataclass(frozen=True)
class PolicyArtifact:
    """Checkpoint plus adapter metadata, saved under ModelStore's rl_retask key.

    model implements MaskablePPO.predict. Training spaces must match this adapter.
    Entity sizes and capability order are metadata, not public tool inputs.
    """

    model: object
    version: str
    max_aircraft: int = 64
    max_missions: int = 64
    max_bases: int = 16
    capabilities: tuple[str, ...] = CAPABILITIES
    observation_version: str = OBSERVATION_VERSION


def validate_artifact(artifact):
    if isinstance(artifact, dict):
        artifact = PolicyArtifact(**artifact)
    if not isinstance(artifact, PolicyArtifact):
        raise ValueError("rl_retask requires a PolicyArtifact or its field dictionary")
    if not isinstance(artifact.version, str) or not artifact.version.strip():
        raise ValueError("Policy version must be a nonempty string")
    if artifact.observation_version != OBSERVATION_VERSION:
        raise ValueError("Unsupported RL observation version")
    for value in (artifact.max_aircraft, artifact.max_missions, artifact.max_bases):
        if type(value) is not int or value <= 0:
            raise ValueError("Policy padding limits must be positive integers")
    if (
        not isinstance(artifact.capabilities, (tuple, list))
        or any(not isinstance(cap, str) or not cap for cap in artifact.capabilities)
        or len(set(artifact.capabilities)) != len(artifact.capabilities)
    ):
        raise ValueError("Policy capability vocabulary must contain unique strings")
    if not callable(getattr(artifact.model, "predict", None)):
        raise ValueError("Policy model must implement predict")
    return artifact


def selection_errors(problem, solution):
    """Independently prove concrete assignments, including historical/frozen data."""
    p = problem.retask
    if solution is None or solution.status in {"INFEASIBLE", "ERROR"}:
        return ["No feasible decision"]
    if p.frozen_conflicts:
        return ["Event conflicts with frozen assignments"]
    if not isfinite(solution.objective):
        return ["Non-finite decision objective"]
    known = {id(c) for c in p.candidates}
    if any(id(c) not in known for c in solution.selected):
        return ["Unknown candidate in decision"]
    actual = [assignment_key(a) for a in solution.assignments]
    expected = [assignment_key(a) for a in p.historical] + [
        assignment_key(c.assignment) for c in solution.selected
    ]
    if sorted(actual) != sorted(expected) or len(set(actual)) != len(actual):
        return ["Assignments do not match the validated decision"]
    return validate_selection(p, solution.selected)


def solve(problem, artifact, *, deadline=None):
    artifact = validate_artifact(artifact)
    env = GarudaRetaskEnv(problem, artifact, deadline=deadline)
    if problem.retask.frozen_conflicts:
        raise ValueError("Event conflicts with frozen assignments")
    space = getattr(artifact.model, "action_space", None)
    if space is not None and getattr(space, "n", None) != env.action_count:
        raise ValueError(
            "Policy action space does not match padded aircraft/mission pairs"
        )
    for _ in range(len(problem.retask.candidates) + 1):
        if deadline is not None and perf_counter() >= deadline:
            raise TimeoutError("Policy decision budget exhausted")
        masks = env.action_masks()
        observation = env.observation()
        obs_space = getattr(artifact.model, "observation_space", None)
        if (
            obs_space is not None
            and getattr(obs_space, "shape", None) != observation.shape
        ):
            raise ValueError("Policy observation space does not match adapter metadata")
        if not masks[1:].any():
            break
        prediction = artifact.model.predict(
            observation,
            deterministic=problem.deterministic,
            action_masks=masks.copy(),
        )
        if deadline is not None and perf_counter() >= deadline:
            raise TimeoutError("Policy decision budget exhausted")
        action = prediction[0] if isinstance(prediction, tuple) else prediction
        value = np.asarray(action)
        if value.size != 1 or value.dtype.kind not in "iu":
            raise ValueError("Policy must return one discrete integer action")
        action = int(value.item())
        if not 0 <= action < len(masks) or not masks[action]:
            raise ValueError("Policy returned a masked or out-of-range action")
        if env.step(action):
            break
    selected = env.selected
    fulfilled = {c.assignment.mission_id for c in selected} | {
        a.mission_id for a in problem.retask.historical
    }
    degraded = (
        {a.mission_id for a in problem.retask.plan.assignments}
        | problem.retask.affected
    ) - fulfilled
    degraded |= {c.assignment.mission_id for c in selected if c.route_replan}
    solution = Solution(
        "FEASIBLE",
        problem.retask.historical + [c.assignment for c in selected],
        selected,
        degraded,
        objective(problem.retask, selected),
    )
    errors = selection_errors(problem, solution)
    if errors:
        raise ValueError("Policy decision failed validation: " + "; ".join(errors))
    return solution
