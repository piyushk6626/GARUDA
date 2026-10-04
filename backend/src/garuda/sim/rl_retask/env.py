"""Bounded inference state for a masked (aircraft, mission) policy.

This is the decision adapter, not a stochastic training simulator. It needs only
NumPy; Gymnasium/SB3 remain optional dependencies of the checkpoint producer.
"""

from collections import Counter, defaultdict
from dataclasses import replace
from time import perf_counter

import numpy as np

from garuda.common import as_utc
from garuda.optimization.retask_rolling_horizon.baseline import validate_selection
from garuda.optimization.retask_rolling_horizon.problem import assignment_key

OBSERVATION_VERSION = "garuda-retask-v1"
STATUSES = ("FMC", "PMC", "NMC", "AOG", "IN_MISSION")
CAPABILITIES = (
    "BVR",
    "WVR",
    "AG",
    "STRIKE",
    "CAS",
    "SEAD",
    "ISR",
    "AEW",
    "AAR",
    "AIRLIFT",
    "SAR",
)


class GarudaRetaskEnv:
    def __init__(self, problem, artifact, *, deadline=None):
        self.problem = problem
        self.artifact = artifact
        self.deadline = deadline
        self.tails = sorted(problem.scenario.aircraft, key=lambda a: a.tail_no)
        active = (
            {c.assignment.mission_id for c in problem.retask.candidates}
            | set(problem.retask.reasons)
            | problem.retask.affected
        )
        self.missions = [problem.retask.missions[mid] for mid in sorted(active)]
        self.bases = sorted(problem.scenario.bases, key=lambda b: b.base_id)
        if (
            len(self.tails) > artifact.max_aircraft
            or len(self.missions) > artifact.max_missions
            or len(self.bases) > artifact.max_bases
        ):
            raise ValueError("Scenario exceeds the policy's padded entity limits")
        known = set(artifact.capabilities)
        if any(set(a.capabilities) - known for a in self.tails) or any(
            set(m.required_capabilities) - known for m in self.missions
        ):
            raise ValueError("Policy capability vocabulary does not cover the scenario")
        locked = {assignment_key(a) for a in problem.retask.locked}
        self.selected = [
            c
            for c in problem.retask.candidates
            if assignment_key(c.assignment) in locked
        ]
        # Partial packages may have fewer than min_aircraft while a policy is
        # adding their remaining tails. All final packages use the real minimum.
        relaxed = {
            mid: m.model_copy(update={"min_aircraft": 1})
            for mid, m in problem.retask.missions.items()
        }
        self.partial_problem = replace(problem.retask, missions=relaxed)
        self.by_pair = defaultdict(list)
        for c in problem.retask.candidates:
            self.by_pair[c.assignment.tail_no, c.assignment.mission_id].append(c)
        old = {assignment_key(a) for a in problem.retask.plan.assignments}
        for group in self.by_pair.values():
            group.sort(
                key=lambda c: (
                    assignment_key(c.assignment) not in old,
                    c.risk,
                    -c.pk,
                    as_utc(c.assignment.tot),
                    c.assignment.config_id,
                )
            )
        self.choices = {}

    @property
    def action_count(self):
        return 1 + self.artifact.max_aircraft * self.artifact.max_missions

    def action_masks(self):
        mask = np.zeros(self.action_count, dtype=bool)
        mask[0] = True  # Skip ends this event's decision sequence.
        self.choices = {}
        selected_keys = {assignment_key(c.assignment) for c in self.selected}
        counts = Counter(c.assignment.mission_id for c in self.selected)
        fulfilled = {
            mid
            for mid, count in counts.items()
            if count >= self.problem.retask.missions[mid].min_aircraft
        } | {a.mission_id for a in self.problem.retask.historical}
        for i, aircraft in enumerate(self.tails):
            for j, mission in enumerate(self.missions):
                if self.deadline is not None and perf_counter() >= self.deadline:
                    raise TimeoutError("Policy decision budget exhausted")
                if (
                    mission.mission_id in self.problem.retask.frozen_missions
                    or counts[mission.mission_id] >= mission.max_aircraft
                    or not set(mission.dependencies) <= fulfilled
                ):
                    continue
                for c in self.by_pair[aircraft.tail_no, mission.mission_id]:
                    if not c.valid or assignment_key(c.assignment) in selected_keys:
                        continue
                    if any(
                        other.assignment.tail_no == aircraft.tail_no
                        and other.assignment.mission_id == mission.mission_id
                        for other in self.selected
                    ):
                        continue
                    if any(
                        other.assignment.mission_id in mission.dependencies
                        and as_utc(other.assignment.tot) > as_utc(c.assignment.tot)
                        for other in self.selected
                    ):
                        continue
                    if validate_selection(
                        self.partial_problem,
                        self.selected + [c],
                        complete=False,
                        deadline=self.deadline,
                    ):
                        continue
                    action = 1 + i * self.artifact.max_missions + j
                    self.choices[action] = c
                    mask[action] = True
                    break
        return mask

    def observation(self):
        """Flatten fixed padded aircraft, mission, base and pair feature blocks."""
        spec = self.artifact
        caps = spec.capabilities
        base_index = {b.base_id: i for i, b in enumerate(self.bases)}
        aircraft_rows = np.zeros(
            (spec.max_aircraft, 8 + spec.max_bases + len(caps)), dtype=np.float32
        )
        for i, a in enumerate(self.tails):
            status = a.status
            if (
                self.problem.retask.busy_until.get(a.tail_no, self.problem.now)
                > self.problem.now
            ):
                status = "IN_MISSION"
            row = aircraft_rows[i]
            row[0] = 1
            row[1:6] = [status == s for s in STATUSES]
            row[6] = a.fuel_kg / max(1, a.fuel_capacity_kg)
            # Event-overlay risk/availability is also represented by pair masks.
            group = [
                c
                for (tail, _), cs in self.by_pair.items()
                if tail == a.tail_no
                for c in cs
            ]
            row[7] = max((c.risk for c in group), default=0)
            if a.base_id in base_index:
                row[8 + base_index[a.base_id]] = 1
            row[8 + spec.max_bases :] = [cap in a.capabilities for cap in caps]
        counts = Counter(c.assignment.mission_id for c in self.selected)
        mission_rows = np.zeros((spec.max_missions, 9 + len(caps)), dtype=np.float32)
        for j, m in enumerate(self.missions):
            group = [
                c
                for (_, mid), cs in self.by_pair.items()
                if mid == m.mission_id
                for c in cs
            ]
            mission_rows[j, :9] = (
                1,
                m.priority / 5,
                m.value / 100,
                (as_utc(m.window_start) - self.problem.now).total_seconds()
                / (72 * 3600),
                (as_utc(m.window_end) - self.problem.now).total_seconds() / (72 * 3600),
                m.min_aircraft / spec.max_aircraft,
                m.max_aircraft / spec.max_aircraft,
                counts[m.mission_id] / max(1, m.max_aircraft),
                max((c.risk for c in group), default=0),
            )
            mission_rows[j, 9:] = [cap in m.required_capabilities for cap in caps]
        base_rows = np.zeros((spec.max_bases, 2), dtype=np.float32)
        for i, b in enumerate(self.bases):
            capacity = [
                v
                for (bid, _), v in self.problem.retask.capacities.items()
                if bid == b.base_id
            ]
            base_rows[i] = (1, min(capacity, default=b.max_sorties_per_hour) / 100)
        pair_rows = np.zeros(
            (spec.max_aircraft, spec.max_missions, 4), dtype=np.float32
        )
        old = {assignment_key(a) for a in self.problem.retask.plan.assignments}
        for action, c in self.choices.items():
            i, j = divmod(action - 1, spec.max_missions)
            pair_rows[i, j] = (1, c.risk, c.pk, assignment_key(c.assignment) in old)
        observation = np.concatenate(
            [
                block.ravel()
                for block in (aircraft_rows, mission_rows, base_rows, pair_rows)
            ]
        )
        if not np.isfinite(observation).all():
            raise ValueError("Non-finite policy observation")
        return observation

    def step(self, action):
        if action == 0:
            return True
        if action not in self.choices:
            raise ValueError("Policy returned an infeasible or padded action")
        self.selected.append(self.choices[action])
        return False
