"""build_problem(payload, ctx) -> MonteCarloProblem (data gathering only).

Each sortie gets a launch probability from its tail's status, a flown path (the plan leg, or a
straight line from its base to the target), its exposure to each threat ring on that path, and a
per-sortie kill probability from its load config. Each mission gets a weather GO probability from
the nearest weather cell at time on target.
"""

from dataclasses import dataclass, field

from shapely import wkt as shapely_wkt

from garuda.contracts.plan import PlanDraft
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.assign_weapons_to_targets.problem import _adjust_pk, _guidance_factor
from garuda.optimization.common.geo import haversine_km
from garuda.sim.common import (
    Point,
    ThreatRing,
    exposure,
    launch_probability,
    nearest_cell,
    threat_rings,
)
from garuda.sim.evaluate_plan_monte_carlo.schemas import EvaluatePlanMonteCarloInput, Stress

KILL_EFFECTS = frozenset({"DESTROY", "SUPPRESS"})
TANKER_CAPABILITY = "AAR_TANKER"
SEAD_SUPPRESSION = 0.3
VALUE_THRESHOLD_FRACTION = 0.8
DEGRADED_WEATHER_FACTOR = 0.6
HEAVY_THREAT_FACTOR = 1.5
HEAVY_THREAT_MIN_CONFIDENCE = 0.8


@dataclass(frozen=True)
class SortieSpec:
    tail_no: str
    mission_idx: int
    p_launch: float
    exposure: tuple[float, ...]
    kill_pk: float
    needs_aar: bool


@dataclass(frozen=True)
class MissionSpec:
    mission_id: str
    value: float
    needs_kill: bool
    min_aircraft: int
    p_go: float
    dependencies: tuple[int, ...]
    suppresses: tuple[int, ...] = ()


@dataclass
class MonteCarloProblem:
    plan: PlanDraft
    missions: list[MissionSpec]
    sorties: list[SortieSpec]
    threats: list[ThreatRing]
    p_tanker: float
    tanker_missions: tuple[int, ...]
    value_threshold: float
    n_runs: int
    seed: int
    stress: Stress = "none"
    order: list[int] = field(default_factory=list)

    @property
    def planned_value(self) -> float:
        return sum(m.value for m in self.missions)


def build_problem(payload: EvaluatePlanMonteCarloInput, ctx: ToolContext) -> MonteCarloProblem:
    plan = _load_plan(ctx, payload.plan_id)
    if not plan.assignments:
        raise ToolInputError(f"plan {payload.plan_id} has no assignments to simulate.")
    scenario = ctx.scenario.snapshot()
    missions_by_id = {m.mission_id: m for m in scenario.missions}
    aircraft_by_tail = {a.tail_no: a for a in scenario.aircraft}
    mission_ids = list(dict.fromkeys(a.mission_id for a in plan.assignments))
    _reject_unknown("mission ids", [m for m in mission_ids if m not in missions_by_id])
    _reject_unknown("tail numbers", sorted({a.tail_no for a in plan.assignments} - set(aircraft_by_tail)))

    targets = {t.target_id: t for t in scenario.targets}
    bases = {b.base_id: b for b in scenario.bases}
    configs = {c.config_id: c for c in scenario.load_configs}
    weapons = {w.weapon_code: w for w in scenario.weapons if w.category != "PODS"}
    legs = {(leg.mission_id, leg.tail_no): leg for leg in plan.legs}
    rings = _stressed_rings(threat_rings(scenario.threats), payload.stress)
    index = {m: i for i, m in enumerate(mission_ids)}

    sorties: list[SortieSpec] = []
    first_tot: dict[str, float] = {}
    for assignment in plan.assignments:
        mission = missions_by_id[assignment.mission_id]
        aircraft = aircraft_by_tail[assignment.tail_no]
        target = targets.get(mission.target_id) if mission.target_id else None
        leg = legs.get((assignment.mission_id, assignment.tail_no))
        path, altitudes = _path(leg, bases.get(aircraft.base_id), target, mission.area_wkt)
        cell = nearest_cell(scenario.cells, path[-1][0], path[-1][1], assignment.tot) if path else None
        sorties.append(
            SortieSpec(
                tail_no=assignment.tail_no,
                mission_idx=index[assignment.mission_id],
                p_launch=launch_probability(aircraft, assignment.depart),
                exposure=tuple(exposure(path, ring, altitudes) for ring in rings),
                kill_pk=_kill_pk(configs.get(assignment.config_id), weapons, target, cell),
                needs_aar=bool(leg and leg.needs_aar),
            )
        )
        stamp = assignment.tot.timestamp()
        first_tot[assignment.mission_id] = min(first_tot.get(assignment.mission_id, stamp), stamp)

    missions = [
        _mission_spec(missions_by_id[m], index, targets, rings, scenario.cells, plan, payload.stress)
        for m in mission_ids
    ]
    tanker_missions = tuple(i for i, m in enumerate(mission_ids) if missions_by_id[m].type == "AAR")
    threshold = payload.value_threshold
    if threshold is None:
        threshold = VALUE_THRESHOLD_FRACTION * sum(m.value for m in missions)
    return MonteCarloProblem(
        plan=plan,
        missions=missions,
        sorties=sorties,
        threats=rings,
        p_tanker=0.0 if payload.stress == "tanker_loss" else _tanker_up(scenario.aircraft, ctx.scenario_time),
        tanker_missions=tanker_missions,
        value_threshold=threshold,
        n_runs=payload.n_runs,
        seed=payload.seed,
        stress=payload.stress,
        order=sorted(range(len(mission_ids)), key=lambda i: (i not in tanker_missions, first_tot[mission_ids[i]])),
    )


def _load_plan(ctx: ToolContext, plan_id: str) -> PlanDraft:
    try:
        return ctx.plans.get(plan_id)
    except KeyError as exc:
        raise ToolInputError(f"unknown plan_id {plan_id}; pass a plan id returned by a planning tool.") from exc


def _reject_unknown(label: str, unknown: list[str]) -> None:
    if unknown:
        raise ToolInputError(f"plan references unknown {label}: {', '.join(unknown)}.")


def _stressed_rings(rings: list[ThreatRing], stress: Stress) -> list[ThreatRing]:
    if stress != "heavy_threat":
        return rings
    return [
        ThreatRing(
            threat_id=r.threat_id,
            lat=r.lat,
            lon=r.lon,
            range_km=r.range_km,
            pk=min(0.95, r.pk * HEAVY_THREAT_FACTOR),
            confidence=max(r.confidence, HEAVY_THREAT_MIN_CONFIDENCE),
            min_alt_ft=r.min_alt_ft,
            max_alt_ft=r.max_alt_ft,
        )
        for r in rings
    ]


def _path(leg, base, target, area_wkt: str | None) -> tuple[list[Point], list[int]]:
    if leg is not None and len(leg.waypoints) >= 2:
        return [(w.lat, w.lon) for w in leg.waypoints], [w.alt_ft for w in leg.waypoints]
    if base is None:
        return [], []
    if target is not None:
        return [(base.lat, base.lon), (target.lat, target.lon)], []
    if area_wkt:
        centre = shapely_wkt.loads(area_wkt).centroid
        return [(base.lat, base.lon), (centre.y, centre.x)], []
    return [], []


def _kill_pk(config, weapons, target, cell) -> float:
    """Chance one sortie's load scores at least one hit on the target."""
    if config is None or target is None:
        return 0.0
    miss = 1.0
    for tokens in config.stations.values():
        for token in tokens:
            code, _, count = token.rpartition("x")
            weapon = weapons.get(code)
            if weapon is None:
                continue
            pk = _adjust_pk(
                weapon.pk_by_target_class.get(target.target_class, 0.0),
                target.hardness,
                target.target_class,
                1.0,
                _guidance_factor(weapon.guidance, cell),
            )
            miss *= (1.0 - pk) ** int(count)
    return 1.0 - miss


def _mission_spec(mission, index, targets, rings, cells, plan, stress: Stress) -> MissionSpec:
    target = targets.get(mission.target_id) if mission.target_id else None
    tot = min(a.tot for a in plan.assignments if a.mission_id == mission.mission_id)
    if target is not None:
        cell = nearest_cell(cells, target.lat, target.lon, tot)
    elif mission.area_wkt:
        centre = shapely_wkt.loads(mission.area_wkt).centroid
        cell = nearest_cell(cells, centre.y, centre.x, tot)
    else:
        cell = None
    p_go = 1.0 if cell is None else cell.flyability
    if stress == "degraded_weather":
        p_go *= DEGRADED_WEATHER_FACTOR
    suppresses: tuple[int, ...] = ()
    if mission.type == "SEAD" and target is not None:
        suppresses = tuple(
            i for i, r in enumerate(rings) if haversine_km(target.lat, target.lon, r.lat, r.lon) <= r.range_km
        )
    sorties_on_mission = sum(1 for a in plan.assignments if a.mission_id == mission.mission_id)
    return MissionSpec(
        mission_id=mission.mission_id,
        value=float(mission.value),
        needs_kill=target is not None and mission.required_effect in KILL_EFFECTS,
        min_aircraft=min(mission.min_aircraft, sorties_on_mission),
        p_go=p_go,
        dependencies=tuple(index[d] for d in mission.dependencies if d in index),
        suppresses=suppresses,
    )


def _tanker_up(aircraft, when) -> float:
    """Chance at least one tanker in the fleet launches."""
    down = 1.0
    for jet in aircraft:
        if TANKER_CAPABILITY in jet.capabilities:
            down *= 1.0 - launch_probability(jet, when)
    return 1.0 - down
