"""Synthetic weapon catalog, stock, and load configs.

Performance figures are round fictional values. Numpy's SeedSequence accepts
integers only, so the weapons stream is SeedSequence([seed, WEAPONS_STREAM]).
That keeps these draws off the threat and mission generator.
"""

import math
from datetime import datetime, timezone
from typing import NamedTuple

import numpy as np

from garuda.config import ScenarioConfig
from garuda.schemas.weapon import (
    TARGET_CLASSES,
    Compatibility,
    Guidance,
    LoadConfig,
    LoadRole,
    WeaponCategory,
    WeaponStock,
    WeaponType,
)

WEAPONS_STREAM = 1
COMMON_CATEGORIES = frozenset({"AAM_WVR", "UNGUIDED"})
COMMON_STOCK_BAND = (200, 1500)
SCARCE_STOCK_BAND = (20, 100)
CLASS_HARDNESS = {
    "RUNWAY": 0.85,
    "BRIDGE": 0.75,
    "ARMOUR": 0.55,
    "RADAR": 0.40,
    "C2": 0.25,
    "FUEL_DEPOT": 0.15,
}
_STOCK_SIGMA = 0.35
_COMMON_MEDIAN = 550.0
_SCARCE_MEDIAN = 45.0
_PENALTY_CAP_PCT = 15.0


class _WeaponSpec(NamedTuple):
    weapon_code: str
    category: WeaponCategory
    range_km: float
    weight_kg: float
    min_release_alt_ft: int
    guidance: Guidance
    pk_low: float
    pk_high: float
    sensitivity: float


class _Load(NamedTuple):
    station: str
    weapon_code: str
    count: int


class _Recipe(NamedTuple):
    role: LoadRole
    loads: tuple[_Load, ...]


class _Airframe(NamedTuple):
    aircraft_type: str
    prefix: str
    external_limit_kg: float
    station_limit_kg: dict[str, float]
    allowed: dict[str, tuple[str, ...]]
    recipes: tuple[_Recipe, ...]


def _loads(*items: tuple[str, str, int]) -> tuple[_Load, ...]:
    return tuple(_Load(station, weapon_code, count) for station, weapon_code, count in items)


_CATALOG = (
    _WeaponSpec("AAM-BVR-MED", "AAM_BVR", 80, 180, 1000, "RADAR", 0.32, 0.50, 0.40),
    _WeaponSpec("AAM-BVR-LONG", "AAM_BVR", 120, 220, 1000, "RADAR", 0.32, 0.50, 0.40),
    _WeaponSpec("AAM-WVR", "AAM_WVR", 20, 90, 500, "IR", 0.32, 0.50, 0.40),
    _WeaponSpec("ASM-STANDOFF", "ASM", 100, 600, 500, "GPS", 0.75, 0.95, 0.15),
    _WeaponSpec("ASM-MED", "ASM", 40, 400, 200, "GPS", 0.75, 0.95, 0.15),
    _WeaponSpec("LGB-500", "LGB", 15, 250, 2000, "LASER", 0.70, 0.92, 0.30),
    _WeaponSpec("LGB-250", "LGB", 10, 125, 1500, "LASER", 0.70, 0.92, 0.30),
    _WeaponSpec("GP-500", "UNGUIDED", 5, 250, 1000, "NONE", 0.65, 0.90, 0.50),
    _WeaponSpec("RKT-70", "UNGUIDED", 4, 80, 200, "NONE", 0.65, 0.90, 0.50),
    _WeaponSpec("ARM-SEAD", "ANTI_RADIATION", 80, 350, 500, "RADAR", 0.70, 0.90, 0.20),
    _WeaponSpec("ASHM-1", "ANTI_SHIP", 70, 500, 200, "RADAR", 0.65, 0.88, 0.20),
    _WeaponSpec("POD-TGT", "PODS", 20, 200, 0, "LASER", 0.30, 0.30, 0.0),
    _WeaponSpec("POD-EW", "PODS", 50, 180, 0, "RADAR", 0.30, 0.30, 0.0),
    _WeaponSpec("POD-ISR", "PODS", 40, 150, 0, "OPTICAL", 0.30, 0.30, 0.0),
)

_SU30_ALLOWED = {
    "AAM-WVR": ("S1", "S2", "S11", "S12"),
    "AAM-BVR-MED": ("S2", "S3", "S6", "S11"),
    "AAM-BVR-LONG": ("S2", "S3", "S6", "S11"),
    "ASM-STANDOFF": ("S3", "S4", "S5", "S6"),
    "ASM-MED": ("S3", "S4", "S5", "S6"),
    "LGB-500": ("S3", "S4", "S5", "S6"),
    "LGB-250": ("S2", "S3", "S4", "S5", "S6"),
    "GP-500": ("S3", "S4", "S5", "S6"),
    "RKT-70": ("S2", "S3", "S6", "S11"),
    "ARM-SEAD": ("S3", "S4", "S5", "S6"),
    "ASHM-1": ("S4", "S5"),
    "POD-TGT": ("S2", "S4", "S5", "S11"),
    "POD-EW": ("S2", "S4", "S11"),
    "POD-ISR": ("S2", "S4", "S5", "S11"),
}
_RAFALE_ALLOWED = {
    "AAM-WVR": ("S1", "S5"),
    "AAM-BVR-MED": ("S2", "S4"),
    "AAM-BVR-LONG": ("S2", "S4"),
    "ASM-MED": ("S3",),
    "ASM-STANDOFF": ("S3",),
    "LGB-500": ("S2", "S3", "S4"),
    "LGB-250": ("S2", "S3", "S4"),
    "ARM-SEAD": ("S3",),
    "POD-EW": ("S2", "S4"),
    "POD-ISR": ("S2", "S3"),
    "POD-TGT": ("S2", "S4"),
}
_TEJAS_ALLOWED = {
    "AAM-WVR": ("S1", "S5"),
    "AAM-BVR-MED": ("S2", "S4"),
    "LGB-250": ("S2", "S3", "S4"),
    "LGB-500": ("S2", "S3", "S4"),
    "GP-500": ("S2", "S3"),
    "RKT-70": ("S2", "S4"),
}
_MIRAGE_ALLOWED = {
    "AAM-WVR": ("S1", "S5"),
    "AAM-BVR-MED": ("S2", "S4"),
    "ASM-MED": ("S3",),
    "LGB-500": ("S2", "S4"),
    "LGB-250": ("S2", "S3", "S4"),
    "GP-500": ("S2", "S4"),
    "ARM-SEAD": ("S3",),
    "POD-EW": ("S2",),
}
_JAGUAR_ALLOWED = {
    "AAM-WVR": ("S1", "S5"),
    "ASM-MED": ("S3",),
    "LGB-500": ("S2", "S3", "S4"),
    "LGB-250": ("S2", "S3", "S4"),
    "GP-500": ("S2", "S4"),
    "ARM-SEAD": ("S2", "S3"),
    "RKT-70": ("S2", "S4"),
}
_HELI_ALLOWED = {
    "RKT-70": ("S1", "S3"),
    "LGB-250": ("S2",),
    "GP-500": ("S2",),
    "POD-ISR": ("S2",),
    "POD-TGT": ("S1",),
}

_AIRFRAMES = (
    _Airframe(
        "Su-30MKI",
        "SU30",
        8000,
        {"S1": 250, "S2": 500, "S3": 1500, "S4": 1500, "S5": 2000, "S6": 1500, "S11": 500, "S12": 250},
        _SU30_ALLOWED,
        (
            _Recipe("DCA", _loads(("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1), ("S2", "AAM-BVR-MED", 1), ("S11", "AAM-BVR-MED", 1))),
            _Recipe("STRIKE", _loads(("S3", "LGB-500", 2), ("S5", "ASM-STANDOFF", 1), ("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1))),
            _Recipe("STRIKE", _loads(("S3", "GP-500", 2), ("S6", "GP-500", 2), ("S4", "LGB-250", 2), ("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1))),
            _Recipe("SEAD", _loads(("S5", "ARM-SEAD", 1), ("S3", "ARM-SEAD", 1), ("S2", "POD-EW", 1), ("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1))),
            _Recipe("CAS", _loads(("S3", "RKT-70", 2), ("S6", "RKT-70", 2), ("S4", "LGB-250", 2), ("S5", "POD-TGT", 1), ("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1))),
            _Recipe("ISR", _loads(("S5", "POD-ISR", 1), ("S2", "POD-TGT", 1), ("S1", "AAM-WVR", 1), ("S12", "AAM-WVR", 1))),
        ),
    ),
    _Airframe(
        "Rafale",
        "RAF",
        6000,
        {"S1": 250, "S2": 1200, "S3": 2000, "S4": 1200, "S5": 250},
        _RAFALE_ALLOWED,
        (
            _Recipe("DCA", _loads(("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1), ("S2", "AAM-BVR-MED", 1), ("S4", "AAM-BVR-MED", 1))),
            _Recipe("STRIKE", _loads(("S3", "ASM-MED", 1), ("S2", "LGB-500", 2), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("SEAD", _loads(("S3", "ARM-SEAD", 1), ("S2", "POD-EW", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("ISR", _loads(("S3", "POD-ISR", 1), ("S2", "POD-TGT", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
        ),
    ),
    _Airframe(
        "Tejas",
        "TEJ",
        3500,
        {"S1": 150, "S2": 800, "S3": 1000, "S4": 800, "S5": 150},
        _TEJAS_ALLOWED,
        (
            _Recipe("DCA", _loads(("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1), ("S2", "AAM-BVR-MED", 1), ("S4", "AAM-BVR-MED", 1))),
            _Recipe("STRIKE", _loads(("S3", "LGB-250", 2), ("S2", "LGB-500", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("CAS", _loads(("S2", "RKT-70", 2), ("S4", "RKT-70", 2), ("S3", "GP-500", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
        ),
    ),
    _Airframe(
        "Mirage-2000",
        "M2K",
        4000,
        {"S1": 200, "S2": 1000, "S3": 1500, "S4": 1000, "S5": 200},
        _MIRAGE_ALLOWED,
        (
            _Recipe("DCA", _loads(("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1), ("S2", "AAM-BVR-MED", 1), ("S4", "AAM-BVR-MED", 1))),
            _Recipe("STRIKE", _loads(("S3", "ASM-MED", 1), ("S2", "LGB-500", 1), ("S4", "LGB-250", 2), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("STRIKE", _loads(("S2", "GP-500", 2), ("S4", "GP-500", 1), ("S3", "LGB-250", 2), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("SEAD", _loads(("S3", "ARM-SEAD", 1), ("S2", "POD-EW", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
        ),
    ),
    _Airframe(
        "Jaguar",
        "JAG",
        4500,
        {"S1": 200, "S2": 1000, "S3": 1500, "S4": 1000, "S5": 200},
        _JAGUAR_ALLOWED,
        (
            _Recipe("STRIKE", _loads(("S3", "ASM-MED", 1), ("S2", "LGB-500", 2), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("STRIKE", _loads(("S2", "GP-500", 2), ("S4", "GP-500", 2), ("S3", "LGB-500", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("SEAD", _loads(("S3", "ARM-SEAD", 1), ("S2", "ARM-SEAD", 1), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
            _Recipe("CAS", _loads(("S2", "RKT-70", 4), ("S4", "RKT-70", 4), ("S3", "LGB-250", 2), ("S1", "AAM-WVR", 1), ("S5", "AAM-WVR", 1))),
        ),
    ),
    _Airframe(
        "Heli",
        "HELI",
        800,
        {"S1": 200, "S2": 400, "S3": 200},
        _HELI_ALLOWED,
        (
            _Recipe("CAS", _loads(("S1", "RKT-70", 2), ("S3", "RKT-70", 2))),
            _Recipe("CAS", _loads(("S2", "LGB-250", 1), ("S1", "RKT-70", 1), ("S3", "RKT-70", 1))),
            _Recipe("CAS", _loads(("S2", "GP-500", 1), ("S1", "RKT-70", 1), ("S3", "RKT-70", 1))),
            _Recipe("ISR", _loads(("S2", "POD-ISR", 1), ("S1", "POD-TGT", 1))),
        ),
    ),
)

MOUNTING_TYPES = frozenset(airframe.aircraft_type for airframe in _AIRFRAMES)
_BY_TYPE = {airframe.aircraft_type: airframe for airframe in _AIRFRAMES}


def build_weapons(
    config: ScenarioConfig,
    seed: int,
) -> tuple[list[WeaponType], list[WeaponStock], list[LoadConfig], list[Compatibility]]:
    rng = np.random.default_rng(np.random.SeedSequence([seed, WEAPONS_STREAM]))
    weapons = _weapon_types(rng)
    stocks = _stocks(config, rng, weapons)
    load_configs = _load_configs(weapons)
    compatibilities = _compatibilities()
    return weapons, stocks, load_configs, compatibilities


def stock_band(category: str) -> tuple[int, int]:
    if category in COMMON_CATEGORIES:
        return COMMON_STOCK_BAND
    return SCARCE_STOCK_BAND


def external_limit_kg(aircraft_type: str) -> float:
    return _BY_TYPE[aircraft_type].external_limit_kg


def station_limit_kg(aircraft_type: str, station: str) -> float:
    return _BY_TYPE[aircraft_type].station_limit_kg[station]


def range_penalty_pct(total_weight_kg: float, limit_kg: float) -> float:
    if limit_kg <= 0:
        raise ValueError("external store limit must be positive")
    scaled = _PENALTY_CAP_PCT * total_weight_kg / limit_kg
    return round(min(_PENALTY_CAP_PCT, scaled), 2)


def _weapon_types(rng: np.random.Generator) -> list[WeaponType]:
    if len(_CATALOG) != 14:
        raise ValueError("weapon catalog must contain 14 types")
    return [
        WeaponType(
            weapon_code=spec.weapon_code,
            category=spec.category,
            range_km=spec.range_km,
            weight_kg=spec.weight_kg,
            pk_by_target_class=_pk_table(spec, rng),
            min_release_alt_ft=spec.min_release_alt_ft,
            guidance=spec.guidance,
        )
        for spec in _CATALOG
    ]


def _pk_table(spec: _WeaponSpec, rng: np.random.Generator) -> dict[str, float]:
    if spec.category == "PODS":
        return {target_class: 0.3 for target_class in TARGET_CLASSES}
    raw = float(rng.uniform(spec.pk_low, spec.pk_high))
    return {
        target_class: _penalized_pk(raw, spec.sensitivity, hardness)
        for target_class, hardness in CLASS_HARDNESS.items()
    }


def _penalized_pk(raw: float, sensitivity: float, hardness: float) -> float:
    penalized = raw * (1.0 - sensitivity * hardness)
    clipped = min(0.9, max(0.3, penalized))
    return round(clipped, 4)


def _stocks(config: ScenarioConfig, rng: np.random.Generator, weapons: list[WeaponType]) -> list[WeaponStock]:
    if config.bases < 1:
        return []
    updated_at = _as_utc(config.start)
    rows: list[WeaponStock] = []
    for weapon in weapons:
        low, high = stock_band(weapon.category)
        median = _COMMON_MEDIAN if weapon.category in COMMON_CATEGORIES else _SCARCE_MEDIAN
        for index in range(1, config.bases + 1):
            rows.append(
                WeaponStock(
                    base_id=f"B{index}",
                    weapon_code=weapon.weapon_code,
                    quantity=_quantity(rng, low, high, median),
                    reserved_qty=0,
                    updated_at=updated_at,
                )
            )
    return rows


def _quantity(rng: np.random.Generator, low: int, high: int, median: float) -> int:
    draw = float(rng.lognormal(math.log(median), _STOCK_SIGMA))
    return int(min(high, max(low, round(draw))))


def _load_configs(weapons: list[WeaponType]) -> list[LoadConfig]:
    weights = {weapon.weapon_code: weapon.weight_kg for weapon in weapons}
    configs: list[LoadConfig] = []
    for airframe in _AIRFRAMES:
        if not 3 <= len(airframe.recipes) <= 6:
            raise ValueError(f"{airframe.aircraft_type} needs 3-6 load configs")
        role_count: dict[str, int] = {}
        for recipe in airframe.recipes:
            role_count[recipe.role] = role_count.get(recipe.role, 0) + 1
            config_id = f"{airframe.prefix}-{recipe.role}-{role_count[recipe.role]}"
            configs.append(_load_config(airframe, config_id, recipe, weights))
    return configs


def _load_config(
    airframe: _Airframe,
    config_id: str,
    recipe: _Recipe,
    weights: dict[str, float],
) -> LoadConfig:
    stations: dict[str, list[str]] = {}
    total = 0.0
    for load in recipe.loads:
        if load.station in stations:
            raise ValueError(f"{config_id} repeats station {load.station}")
        limit = airframe.station_limit_kg.get(load.station)
        if limit is None:
            raise ValueError(f"{config_id} uses unknown station {load.station}")
        permitted = airframe.allowed.get(load.weapon_code)
        if permitted is None or load.station not in permitted:
            raise ValueError(f"{config_id} puts {load.weapon_code} on {load.station}")
        if load.weapon_code not in weights:
            raise ValueError(f"{config_id} uses unknown weapon {load.weapon_code}")
        station_weight = weights[load.weapon_code] * load.count
        if station_weight > limit:
            raise ValueError(f"{config_id} overloads {load.station}")
        stations[load.station] = [f"{load.weapon_code}x{load.count}"]
        total += station_weight
    if total > airframe.external_limit_kg:
        raise ValueError(f"{config_id} exceeds the external store limit")
    return LoadConfig(
        config_id=config_id,
        aircraft_type=airframe.aircraft_type,
        role=recipe.role,
        stations=stations,
        total_weight_kg=total,
        range_penalty_pct=range_penalty_pct(total, airframe.external_limit_kg),
    )


def _compatibilities() -> list[Compatibility]:
    codes = [spec.weapon_code for spec in _CATALOG]
    rows: list[Compatibility] = []
    for airframe in _AIRFRAMES:
        for station, limit in airframe.station_limit_kg.items():
            if limit <= 0:
                raise ValueError(f"{airframe.aircraft_type} station {station} has no capacity")
        for weapon_code in codes:
            allowed = airframe.allowed.get(weapon_code)
            if allowed is None:
                continue
            unknown = [station for station in allowed if station not in airframe.station_limit_kg]
            if unknown:
                raise ValueError(f"{airframe.aircraft_type} allows {weapon_code} on {unknown}")
            rows.append(
                Compatibility(
                    aircraft_type=airframe.aircraft_type,
                    weapon_code=weapon_code,
                    allowed_stations=list(allowed),
                )
            )
    return rows


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
