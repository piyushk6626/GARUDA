"""Vectorised replications with chronological launch and mission events.

WeatherGoProvider yields GO/NO_GO; degraded-weather stress multiplies p_go by
0.75. PkProvider represents the whole load configuration: one Bernoulli is
equivalent to at least one weapon hit, avoiding double application of Pk.
Internal constants: turnaround lognormal sigma=0.25, Beta concentration=20,
heavy-threat multiplier=1.5, successful SEAD suppression multiplier=0.25.
"""

from dataclasses import dataclass

import numpy as np

from garuda.sim.evaluate_plan_monte_carlo.problem import Problem


@dataclass
class Solution:
    value: np.ndarray
    mission_success: np.ndarray
    losses: np.ndarray
    factors: dict[str, np.ndarray]


def _beta(mean: float, n: int, rng: np.random.Generator) -> np.ndarray:
    if mean in (0.0, 1.0):
        return np.full(n, mean)
    return rng.beta(mean * 20, (1 - mean) * 20, n)


def solve(problem: Problem, options=None) -> Solution:
    """Pure simulation; memory is O(n_runs * (aircraft + sorties + threats))."""
    rng = np.random.default_rng(problem.seed)
    n = problem.n_runs
    missions = {m.mission_id: m for m in problem.missions}
    indices = {mid: i for i, mid in enumerate(missions)}
    success = np.zeros((n, len(missions)), dtype=bool)
    achieved = {mid: np.full(n, np.inf) for mid in missions}
    support_end = {mid: np.full(n, -np.inf) for mid in missions}
    tails = sorted({a.tail_no for s in problem.sorties for a in s.aircraft})
    alive = {tail: np.ones(n, dtype=bool) for tail in tails}
    ready = {tail: np.full(n, -np.inf) for tail in tails}
    airborne_until = {tail: np.full(n, -np.inf) for tail in tails}
    weather = {day: rng.random(n) for day in sorted({s.day for s in problem.sorties})}
    launch_draws = {}
    launch_time = None
    strength = {t.threat_id: _beta(t.pk_base, n, rng) * _beta(t.confidence, n, rng)
                * (0.25 if t.status == "SUPPRESSED" else 1.0) for t in problem.threats}
    state = {}
    losses = np.zeros(n)
    factors = {name: np.zeros(n) for name in ("availability", "weather", "delay", "threat", "weapon_effect")}
    # Resolve equal-TOT dependencies before their consumers, independent of IDs.
    dependency_depth = {}

    def depth(mid):
        if mid not in dependency_depth:
            dependency_depth[mid] = 1 + max((depth(dep) for dep in missions[mid].dependencies
                                              if dep in missions), default=-1)
        return dependency_depth[mid]

    events = [(s.depart, 1, 0, i) for i, s in enumerate(problem.sorties)]
    events += [(s.tot, 2, depth(s.mission_id), i) for i, s in enumerate(problem.sorties)]
    events.sort()
    final_sortie = {mid: max((i for i, s in enumerate(problem.sorties) if s.mission_id == mid),
                            key=lambda i: (problem.sorties[i].tot, i))
                    for mid in missions if any(s.mission_id == mid for s in problem.sorties)}
    mission_sorties = {mid: [i for i, s in enumerate(problem.sorties) if s.mission_id == mid]
                       for mid in missions}
    for _, event, _, i in events:
        sortie = problem.sorties[i]
        mission = missions[sortie.mission_id]
        if event == 1:
            if launch_time != sortie.depart:
                launch_draws.clear()
                launch_time = sortie.depart
            chosen = np.full(n, -1, dtype=int)
            delay = np.zeros(n)
            p_failure = np.zeros(n)
            go_probability = sortie.p_go * (0.75 if problem.stress == "degraded_weather" else 1.0)
            go = weather[sortie.day] < go_probability
            for j, aircraft in enumerate(sortie.aircraft):
                key = (aircraft.tail_no, sortie.depart)
                if key not in launch_draws:
                    launch_draws[key] = rng.random(n)
                launch = launch_draws[key] < aircraft.p_launch
                # A primary still turning around may launch late, but an
                # airborne aircraft cannot be queued onto another sortie.
                # Spares must already be ready at the scheduled departure.
                free = (ready[aircraft.tail_no] if j else airborne_until[aircraft.tail_no]) <= sortie.depart
                use = (chosen < 0) & alive[aircraft.tail_no] & launch & free & go
                preparation = rng.lognormal(np.log(aircraft.turnaround_min) - 0.25 ** 2 / 2,
                                            0.25, n)
                slip = np.maximum(0, ready[aircraft.tail_no] - sortie.depart)
                slip += np.maximum(0, preparation - aircraft.turnaround_min)
                use &= (sortie.tot + slip >= mission.window_start.timestamp() / 60)
                use &= (sortie.tot + slip <= mission.window_end.timestamp() / 60)
                chosen[use] = j
                delay[use] = slip[use]
                p_failure[use] = aircraft.p_failure
                ready[aircraft.tail_no][use] = sortie.end + slip[use] + preparation[use]
                airborne_until[aircraft.tail_no][use] = sortie.end + slip[use]
            state[i] = {"chosen": chosen, "delay": delay, "p_failure": p_failure}
            factors["availability"] += chosen >= 0
            factors["weather"] += go
            factors["delay"] += delay
            continue

        flight = state[i]
        launched = flight["chosen"] >= 0
        actual_tot = sortie.tot + flight["delay"]
        supported = np.ones(n, dtype=bool)
        suppression = np.ones(n)
        aar_dependencies = []
        for dependency in mission.dependencies:
            if dependency not in missions:
                supported[:] = False
                continue
            dep = missions[dependency]
            available = success[:, indices[dependency]] & (achieved[dependency] <= actual_tot)
            if dep.type == "SEAD":
                # Failed SEAD leaves the threat unsuppressed; the strike may
                # still succeed rather than being automatically cancelled.
                suppression[available] *= 0.25
            elif dep.type == "AAR":
                aar_dependencies.append(dependency)
                supported &= available & (support_end[dependency] >= actual_tot)
            else:
                supported &= available
        if sortie.needs_aar and not aar_dependencies:
            tanker_available = np.zeros(n, dtype=bool)
            for mid, other in missions.items():
                if other.type == "AAR":
                    tanker_available |= success[:, indices[mid]] & (achieved[mid] <= actual_tot) & (support_end[mid] >= actual_tot)
            supported &= tanker_available
        if problem.stress == "tanker_loss":
            if mission.type == "AAR" or sortie.needs_aar or aar_dependencies:
                supported[:] = False
        survival = 1 - flight["p_failure"]
        threat_risk = np.zeros(n)
        for threat_id, hours in sortie.exposures:
            risk = np.clip(strength[threat_id] * hours * suppression
                           * (1.5 if problem.stress == "heavy_threat" else 1.0), 0, 1)
            survival *= 1 - risk
            threat_risk += risk
        dead = launched & (rng.random(n) >= survival)
        if problem.stress == "tanker_loss" and mission.type == "AAR":
            dead |= launched
        for j, aircraft in enumerate(sortie.aircraft):
            alive[aircraft.tail_no][dead & (flight["chosen"] == j)] = False
        losses += dead
        effective = rng.random(n) < sortie.p_effect
        flight["survived"] = launched & ~dead & supported
        flight["effective"] = flight["survived"] & effective
        factors["threat"] += threat_risk
        factors["weapon_effect"] += effective
        if final_sortie[sortie.mission_id] == i:
            flights = [state[j] for j in mission_sorties[sortie.mission_id]]
            survivors = sum(f["survived"] for f in flights)
            effect = np.logical_or.reduce([f["effective"] for f in flights])
            succeeded = (survivors >= mission.min_aircraft) & effect
            success[:, indices[sortie.mission_id]] = succeeded
            completion = np.maximum.reduce([problem.sorties[j].tot + state[j]["delay"]
                                            for j in mission_sorties[sortie.mission_id]])
            achieved[sortie.mission_id][succeeded] = completion[succeeded]
            ends = np.maximum.reduce([np.where(state[j]["survived"],
                                               problem.sorties[j].end + state[j]["delay"], -np.inf)
                                      for j in mission_sorties[sortie.mission_id]])
            support_end[sortie.mission_id][succeeded] = ends[succeeded]
    count = max(1, len(problem.sorties))
    factors = {name: values / count for name, values in factors.items()}
    values = np.array([m.value for m in problem.missions])
    return Solution(success @ values, success, losses, factors)
