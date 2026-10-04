"""Build a static weapon-target assignment from the scenario snapshot.

Pk starts at ``weapon_type.pk_by_target_class`` and is then scaled for this
target's hardness, threat exposure, and a guidance penalty from the nearest
weather cell. Pods are not munitions and are left out. A weapon is eligible
from a base only when that base still has unreserved stock and the target is
inside the weapon's range.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from garuda.common import as_utc
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.optimization.assign_weapons_to_targets.schemas import AssignWeaponsToTargetsInput
from garuda.schemas.weapon import TARGET_CLASSES

X_MAX = 4
EPS = 1e-6
PK_CAP = 0.95
HARDNESS_WEIGHT = 0.35
THREAT_FLOOR = 0.15
WEATHER_FLOOR = 0.2
# Class-typical hardness used when the catalog Pk was built. A target at this
# hardness keeps the catalog Pk; a harder target is scaled down.
CLASS_HARDNESS = {
    "RUNWAY": 0.85,
    "BRIDGE": 0.75,
    "ARMOUR": 0.55,
    "RADAR": 0.40,
    "C2": 0.25,
    "FUEL_DEPOT": 0.15,
}
CLOUD_SENSITIVITY = {
    "LASER": 0.55,
    "IR": 0.40,
    "OPTICAL": 0.45,
    "GPS": 0.08,
    "RADAR": 0.05,
    "NONE": 0.20,
}
THREAT_WEIGHT = {
    "ACTIVE": 1.0,
    "UNKNOWN": 0.6,
    "SUPPRESSED": 0.25,
    "DESTROYED": 0.0,
}
LAUNCHABLE = frozenset({"FMC", "PMC"})
SHORTFALL_WEIGHT = 1_000_000.0

AssignmentKey = tuple[str, str, str]  # base_id, weapon_code, target_id
StockKey = tuple[str, str]  # base_id, weapon_code


@dataclass
class WTAProblem:
    target_ids: tuple[str, ...]
    values: dict[str, float]
    min_destroy_prob: dict[str, float]
    cost: dict[str, float]
    stock: dict[StockKey, int]
    pk: dict[AssignmentKey, float]
    platform_capacity: dict[str, int] | None = None
    x_max: int = X_MAX
    eps: float = EPS


@dataclass(frozen=True)
class WTASolution:
    """Positive assignments as (base_id, weapon_code, target_id, count)."""

    counts: tuple[tuple[str, str, str, int], ...]


def build_problem(payload: AssignWeaponsToTargetsInput, ctx: ToolContext) -> WTAProblem:
    if not payload.target_ids:
        raise ToolInputError("target_ids is empty; pass at least one target id.")
    scenario = ctx.scenario.snapshot()
    target_ids = tuple(dict.fromkeys(payload.target_ids))
    targets_by_id = {target.target_id: target for target in scenario.targets}
    unknown = [target_id for target_id in target_ids if target_id not in targets_by_id]
    if unknown:
        joined = ", ".join(unknown)
        raise ToolInputError(f"Unknown target_ids: {joined}. Use target ids from the scenario.")
    thresholds = _thresholds(payload.min_destroy_prob, target_ids)
    _reject_unknown_bases(payload.base_ids, scenario)

    weapons = {
        weapon.weapon_code: weapon
        for weapon in scenario.weapons
        if weapon.category != "PODS"
    }
    stock = _available_stock(scenario, payload.base_ids, set(weapons))
    targets = [targets_by_id[target_id] for target_id in target_ids]
    pk = _kill_probabilities(
        targets,
        weapons,
        stock,
        list(getattr(scenario, "threats", []) or []),
        list(getattr(scenario, "cells", []) or []),
        list(getattr(scenario, "bases", []) or []),
        list(getattr(scenario, "distances", []) or []),
        payload.scenario_time,
    )
    return WTAProblem(
        target_ids=target_ids,
        values={target.target_id: float(target.value) for target in targets},
        min_destroy_prob=thresholds,
        cost={code: 1.0 for code in weapons},
        stock=stock,
        pk=pk,
        platform_capacity=_platform_capacity(scenario, set(stock)),
    )


def freeze_counts(counts: dict[AssignmentKey, int]) -> WTASolution:
    rows = tuple(
        sorted(
            (
                (base_id, weapon_code, target_id, count)
                for (base_id, weapon_code, target_id), count in counts.items()
                if count > 0
            ),
            key=lambda row: (row[2], row[1], row[0]),
        )
    )
    return WTASolution(counts=rows)


def counts_map(solution: WTASolution) -> dict[AssignmentKey, int]:
    return {
        (base_id, weapon_code, target_id): count
        for base_id, weapon_code, target_id, count in solution.counts
    }


def survival_by_target(problem: WTAProblem, counts: dict[AssignmentKey, int]) -> dict[str, float]:
    log_survival = {target_id: 0.0 for target_id in problem.target_ids}
    for (base_id, weapon_code, target_id), count in counts.items():
        if count <= 0 or target_id not in log_survival:
            continue
        pk = problem.pk[(base_id, weapon_code, target_id)]
        log_survival[target_id] += count * math.log1p(-pk)
    return {target_id: math.exp(log_s) for target_id, log_s in log_survival.items()}


def destroyed_value(problem: WTAProblem, survival: dict[str, float]) -> float:
    return sum(problem.values[target_id] * (1.0 - survival[target_id]) for target_id in problem.target_ids)


def penalized_value(problem: WTAProblem, survival: dict[str, float]) -> float:
    """Destroyed value minus a large penalty for missed coverage thresholds."""
    penalty = 0.0
    for target_id, theta in problem.min_destroy_prob.items():
        shortfall = theta - (1.0 - survival[target_id])
        if shortfall > 0:
            penalty += shortfall
    return destroyed_value(problem, survival) - SHORTFALL_WEIGHT * penalty


def _thresholds(raw: dict[str, float], target_ids: tuple[str, ...]) -> dict[str, float]:
    known = set(target_ids)
    unknown = sorted(target_id for target_id in raw if target_id not in known)
    if unknown:
        joined = ", ".join(unknown)
        raise ToolInputError(
            f"min_destroy_prob names targets that were not requested: {joined}."
        )
    thresholds: dict[str, float] = {}
    for target_id, theta in raw.items():
        if theta < 0 or theta > 1:
            raise ToolInputError(f"min_destroy_prob for {target_id} must be between 0 and 1.")
        if theta > 0:
            thresholds[target_id] = float(theta)
    return thresholds


def _reject_unknown_bases(base_ids: list[str] | None, scenario) -> None:
    if not base_ids:
        return
    known = {base.base_id for base in getattr(scenario, "bases", []) or []}
    known.update(row.base_id for row in scenario.stocks)
    unknown = [base_id for base_id in base_ids if base_id not in known]
    if unknown:
        joined = ", ".join(unknown)
        raise ToolInputError(f"Unknown base_ids: {joined}. Use base ids from the scenario.")


def _available_stock(scenario, base_ids: list[str] | None, weapon_codes: set[str]) -> dict[StockKey, int]:
    selected = set(base_ids) if base_ids is not None else None
    stock: dict[StockKey, int] = {}
    for row in scenario.stocks:
        if row.weapon_code not in weapon_codes:
            continue
        if selected is not None and row.base_id not in selected:
            continue
        available = row.quantity - row.reserved_qty
        if available <= 0:
            continue
        key = (row.base_id, row.weapon_code)
        stock[key] = stock.get(key, 0) + available
    return stock


def _kill_probabilities(targets, weapons, stock, threats, cells, bases, distances, when: datetime):
    separation = _separations(distances)
    pk: dict[AssignmentKey, float] = {}
    for target in targets:
        delivery = _delivery_factor(target, threats)
        cell = _nearest_cell(cells, target.lat, target.lon, when)
        for weapon in weapons.values():
            adjusted = _adjust_pk(
                weapon.pk_by_target_class[target.target_class],
                target.hardness,
                target.target_class,
                delivery,
                _guidance_factor(weapon.guidance, cell),
            )
            if adjusted <= 0:
                continue
            for base_id, weapon_code in stock:
                if weapon_code != weapon.weapon_code:
                    continue
                distance_km = _separation_km(separation, bases, base_id, target)
                if distance_km is not None and distance_km > weapon.range_km:
                    continue
                pk[(base_id, weapon.weapon_code, target.target_id)] = adjusted
    return pk


def _adjust_pk(catalog_pk: float, hardness: float, target_class: str, delivery: float, weather: float) -> float:
    if target_class not in TARGET_CLASSES:
        return 0.0
    typical = CLASS_HARDNESS.get(target_class, 0.5)
    denom = max(0.05, 1.0 - HARDNESS_WEIGHT * typical)
    scale = (1.0 - HARDNESS_WEIGHT * hardness) / denom
    adjusted = catalog_pk * scale * delivery * weather
    if adjusted <= 0:
        return 0.0
    return min(PK_CAP, adjusted)


def _delivery_factor(target, threats) -> float:
    survive = 1.0
    for threat in threats:
        weight = THREAT_WEIGHT.get(threat.status, 0.0)
        if weight <= 0:
            continue
        distance_km = _distance_km(target.lat, target.lon, threat.lat, threat.lon)
        if distance_km > threat.engagement_range_km:
            continue
        hit = min(0.95, weight * threat.pk_base * threat.confidence)
        survive *= 1.0 - hit
    return max(THREAT_FLOOR, survive)


def _guidance_factor(guidance: str, cell) -> float:
    if cell is None:
        return 1.0
    cloud = cell.cloud_low_pct / 100.0
    sensitivity = CLOUD_SENSITIVITY.get(guidance, 0.2)
    visibility = 1.0 if cell.visibility_km >= 8 else max(0.5, cell.visibility_km / 8.0)
    factor = cell.flyability * (1.0 - sensitivity * cloud) * visibility
    return min(1.0, max(WEATHER_FLOOR, factor))


def _nearest_cell(cells, lat: float, lon: float, when: datetime):
    if not cells:
        return None

    moment = as_utc(when)

    def sort_key(cell):
        delta_s = abs((as_utc(cell.time) - moment).total_seconds())
        return (delta_s, _distance_km(lat, lon, cell.lat, cell.lon))

    return min(cells, key=sort_key)


def _separations(distances) -> dict[tuple[str, str], float]:
    found: dict[tuple[str, str], float] = {}
    for row in distances:
        found[(row.base_id, row.target_id)] = row.distance_km
    return found


def _separation_km(separation, bases, base_id: str, target) -> float | None:
    known = separation.get((base_id, target.target_id))
    if known is not None:
        return known
    for base in bases:
        if base.base_id == base_id:
            return _distance_km(base.lat, base.lon, target.lat, target.lon)
    return None


def _platform_capacity(scenario, stock: dict[StockKey, int]) -> dict[str, int] | None:
    aircraft = list(getattr(scenario, "aircraft", []) or [])
    load_configs = list(getattr(scenario, "load_configs", []) or [])
    if not aircraft or not load_configs:
        return None
    stores_by_type: dict[str, int] = {}
    for config in load_configs:
        stores = _store_count(config)
        stores_by_type[config.aircraft_type] = max(stores_by_type.get(config.aircraft_type, 0), stores)
    caps: dict[str, int] = defaultdict(int)
    for jet in aircraft:
        if jet.status not in LAUNCHABLE:
            continue
        if not any(base_id == jet.base_id for base_id, _weapon in stock):
            continue
        caps[jet.base_id] += stores_by_type.get(jet.type, 0)
    return dict(caps)


def _store_count(config) -> int:
    total = 0
    for tokens in config.stations.values():
        for token in tokens:
            total += int(token.rsplit("x", 1)[1])
    return total


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    chord = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(min(1.0, chord)))
