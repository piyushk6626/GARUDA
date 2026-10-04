"""solve(problem) -> Simulation: vectorised over runs, pure, sync, picklable inputs.

Per run: ground aborts, correlated weather, realised threat strength (Beta noise on confidence),
attrition along each sortie's path, weapon hits, then cascades in time-on-target order: a
successful SEAD mission suppresses the threats around its target for later missions, a failed
tanker drops AAR-dependent sorties, and a failed dependency fails the missions that need it.
"""

from dataclasses import dataclass

import numpy as np

from garuda.sim import kernel
from garuda.sim.evaluate_plan_monte_carlo.problem import SEAD_SUPPRESSION, MonteCarloProblem

CONFIDENCE_CONCENTRATION = 20.0


@dataclass
class Simulation:
    outcomes: kernel.Outcomes
    factors: dict[str, np.ndarray]


def solve(problem: MonteCarloProblem, options=None) -> Simulation:
    _ = options
    rng = np.random.default_rng(problem.seed)
    n = problem.n_runs
    plan = problem.plan
    launched = kernel.sample_launch(plan, _launch_odds(problem), n, rng)
    go = kernel.sample_weather(plan, {m.mission_id: m.p_go for m in problem.missions}, n, rng)
    strength = _threat_strength(problem, n, rng)
    tanker_up = rng.random(n) < problem.p_tanker
    if problem.tanker_missions:
        tanker_up = np.zeros(n, dtype=bool)

    suppression = np.ones((n, len(problem.threats)))
    pk = np.array([t.pk for t in problem.threats], dtype=float)
    success = np.zeros((n, len(problem.missions)), dtype=bool)
    losses = np.zeros(n)
    by_mission = [[s for s, sortie in enumerate(problem.sorties) if sortie.mission_idx == m] for m in range(len(problem.missions))]

    for m in problem.order:
        mission = problem.missions[m]
        arrived_count = np.zeros(n, dtype=int)
        hit = np.zeros(n, dtype=bool)
        for s in by_mission[m]:
            sortie = problem.sorties[s]
            flew = launched[:, s] & go[:, m]
            if sortie.needs_aar:
                flew &= tanker_up
            exposure = np.asarray(sortie.exposure, dtype=float)
            survive = np.prod(1.0 - np.minimum(0.95, pk * strength * suppression * exposure), axis=1)
            lost = flew & (rng.random(n) >= survive)
            losses += lost
            arrived = flew & ~lost
            arrived_count += arrived
            if mission.needs_kill:
                hit |= arrived & (rng.random(n) < sortie.kill_pk)
        done = arrived_count >= mission.min_aircraft
        if mission.needs_kill:
            done &= hit
        for d in mission.dependencies:
            done &= success[:, d]
        success[:, m] = done
        if mission.suppresses:
            cols = list(mission.suppresses)
            suppression[:, cols] = np.where(done[:, None], SEAD_SUPPRESSION, suppression[:, cols])
        if m in problem.tanker_missions and problem.stress != "tanker_loss":
            tanker_up |= done

    values = np.array([mission.value for mission in problem.missions], dtype=float)
    outcomes = kernel.Outcomes(value=success @ values, mission_success=success, losses=losses)
    factors = {
        "weather": go.mean(axis=1),
        "threat_strength": strength.mean(axis=1) if problem.threats else np.zeros(n),
        "aircraft_availability": launched.mean(axis=1),
        "tanker": tanker_up.astype(float),
    }
    return Simulation(outcomes=outcomes, factors=factors)


def _launch_odds(problem: MonteCarloProblem) -> dict[str, float]:
    return {sortie.tail_no: sortie.p_launch for sortie in problem.sorties}


def _threat_strength(problem: MonteCarloProblem, n: int, rng: np.random.Generator) -> np.ndarray:
    """(n, n_threats) realised confidence, Beta-distributed around each threat's reported confidence."""
    if not problem.threats:
        return np.zeros((n, 0))
    mean = np.clip([t.confidence for t in problem.threats], 0.02, 0.98)
    return rng.beta(mean * CONFIDENCE_CONCENTRATION, (1.0 - mean) * CONFIDENCE_CONCENTRATION, size=(n, len(mean)))
