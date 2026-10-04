"""Synthetic fleet, squadrons, and the T0 readiness snapshot.

Performance figures in the type catalog are fictional planning parameters.
Draws use SeedSequence([seed, stream]) so they stay off the mission generator.
"""

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

from garuda.bases import BASES
from garuda.config import ScenarioConfig
from garuda.schemas.aircraft import (
    FLEET_TYPES,
    Aircraft,
    AircraftHistory,
    AircraftTypeSpec,
    Squadron,
    canonical_type,
)

CATALOG_PATH = Path(__file__).resolve().parents[4] / "config" / "aircraft_types.yaml"
STATE_STREAM = 21
REPAIR_STREAM = 22
OLDER_TYPES = frozenset({"Mirage-2000", "Jaguar"})
MODERN_WEIGHTS = (0.75, 0.10, 0.15)
OLDER_WEIGHTS = (0.70, 0.12, 0.18)
OLDER_REPAIR_FACTOR = 1.4
HOME_BASE = {
    "Su-30MKI": "B1",
    "Rafale": "B4",
    "Tejas": "B3",
    "Mirage-2000": "B5",
    "Jaguar": "B2",
    "C-130J": "B6",
    "IL-78": "B7",
    "AEW&C": "B7",
    "helicopter": "B6",
}
TAIL_PREFIX = {
    "Su-30MKI": "SU",
    "Rafale": "RF",
    "Tejas": "TJ",
    "Mirage-2000": "MG",
    "Jaguar": "JG",
    "C-130J": "C130",
    "IL-78": "IL",
    "AEW&C": "AEW",
    "helicopter": "HL",
}
_BASE_IDS = [airfield.base_id for airfield in BASES]
_NMC_REASONS = ("radar_fault", "scheduled_inspection")


def load_type_catalog(path: Path | None = None) -> dict[str, AircraftTypeSpec]:
    catalog_path = path or CATALOG_PATH
    raw = yaml.safe_load(catalog_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"aircraft catalog must be a mapping: {catalog_path}")
    catalog: dict[str, AircraftTypeSpec] = {}
    for name, values in raw.items():
        canonical = canonical_type(str(name))
        if not isinstance(values, dict):
            raise ValueError(f"aircraft type {name} must be a mapping")
        catalog[canonical] = AircraftTypeSpec(type=canonical, **values)
    missing = [name for name in FLEET_TYPES if name not in catalog]
    if missing:
        raise ValueError(f"aircraft catalog is missing {', '.join(missing)}")
    return catalog


def fleet_counts(fleet: dict[str, int]) -> dict[str, int]:
    counts = {name: 0 for name in FLEET_TYPES}
    for name, count in fleet.items():
        if count < 0:
            raise ValueError(f"fleet count for {name} is negative")
        counts[canonical_type(name)] += count
    return {name: count for name, count in counts.items() if count}


def allocate_counts(total: int, weights: tuple[float, ...]) -> list[int]:
    if total < 0:
        raise ValueError("total must be non-negative")
    if not weights or any(weight < 0 for weight in weights):
        raise ValueError("weights must be non-negative")
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive value")
    exact = [total * weight / weight_sum for weight in weights]
    counts = [int(value) for value in exact]
    remainder = total - sum(counts)
    order = sorted(
        range(len(weights)),
        key=lambda index: (exact[index] - counts[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def failure_hazard(hours_since_inspection: float, *, surge: float = 1.0) -> float:
    """Per flight-hour failure hazard. It rises with time since inspection."""
    if hours_since_inspection < 0 or surge <= 0:
        raise ValueError("hazard inputs must be non-negative and surge must be positive")
    return 0.002 * (1.0 + hours_since_inspection / 100.0) * surge


def hazard_probability(
    hours_since_inspection: float,
    dt_hours: float,
    *,
    surge: float = 1.0,
) -> float:
    if dt_hours < 0:
        raise ValueError("dt_hours must be non-negative")
    rate = failure_hazard(hours_since_inspection, surge=surge)
    return 1.0 - math.exp(-rate * dt_hours)


def build_aircraft(
    config: ScenarioConfig,
    seed: int,
    catalog: dict[str, AircraftTypeSpec] | None = None,
) -> tuple[list[AircraftTypeSpec], list[Squadron], list[Aircraft], list[AircraftHistory]]:
    specs = catalog or load_type_catalog()
    counts = fleet_counts(config.fleet)
    start = _as_utc(config.start)
    state_rng = np.random.default_rng(np.random.SeedSequence([seed, STATE_STREAM]))
    repair_rng = np.random.default_rng(np.random.SeedSequence([seed, REPAIR_STREAM]))
    squadrons = [_squadron(name, specs[name]) for name in FLEET_TYPES if counts.get(name, 0)]
    squadron_by_type = {squadron.type: squadron for squadron in squadrons}
    aircraft: list[Aircraft] = []
    for name in FLEET_TYPES:
        count = counts.get(name, 0)
        if not count:
            continue
        spec = specs[name]
        older = name in OLDER_TYPES
        weights = OLDER_WEIGHTS if older else MODERN_WEIGHTS
        fmc, pmc, nmc = allocate_counts(count, weights)
        statuses = ["FMC"] * fmc + ["PMC"] * pmc + ["NMC"] * nmc
        state_rng.shuffle(statuses)
        squadron = squadron_by_type[name]
        for number, status in enumerate(statuses, start=1):
            aircraft.append(
                _airframe(
                    spec,
                    squadron,
                    number,
                    status,
                    start,
                    state_rng,
                    repair_rng,
                    older=older,
                )
            )
    aircraft.sort(key=lambda item: item.tail_no)
    history = [_history(item) for item in aircraft]
    return [specs[name] for name in FLEET_TYPES], squadrons, aircraft, history


def _squadron(name: str, spec: AircraftTypeSpec) -> Squadron:
    return Squadron(
        sqn_id=f"SQ-{TAIL_PREFIX[name]}",
        type=spec.type,
        home_base_id=HOME_BASE[name],
        name=f"{name} Squadron",
    )


def _airframe(
    spec: AircraftTypeSpec,
    squadron: Squadron,
    number: int,
    status: str,
    start: datetime,
    state_rng: np.random.Generator,
    repair_rng: np.random.Generator,
    *,
    older: bool,
) -> Aircraft:
    hours_since = float(state_rng.uniform(0.0, spec.inspection_cycle_hours))
    total = float(state_rng.gamma(4.0, 1500.0))
    while total < hours_since:
        total = float(state_rng.gamma(4.0, 1500.0))
    fuel = float(state_rng.uniform(spec.fuel_capacity_kg * 0.35, spec.fuel_capacity_kg * 0.95))
    reason = None
    eta = None
    if status == "PMC":
        reason = "deferred_defect"
    elif status == "NMC":
        reason = _NMC_REASONS[(number - 1) % len(_NMC_REASONS)]
        repair_hours = float(repair_rng.lognormal(math.log(18.0), 0.7))
        if older:
            repair_hours *= OLDER_REPAIR_FACTOR
        eta = start + timedelta(hours=repair_hours)
    base_id = squadron.home_base_id
    if number % 5 == 0:
        home_index = _BASE_IDS.index(squadron.home_base_id)
        base_id = _BASE_IDS[(home_index + 1) % len(_BASE_IDS)]
    return Aircraft(
        tail_no=f"{TAIL_PREFIX[spec.type]}-A{number:04d}",
        type=spec.type,
        sqn_id=squadron.sqn_id,
        base_id=base_id,
        status=status,
        status_reason=reason,
        fuel_kg=fuel,
        fuel_capacity_kg=spec.fuel_capacity_kg,
        flight_hours_total=total,
        hours_to_next_inspection=spec.inspection_cycle_hours - hours_since,
        inspection_cycle_hours=spec.inspection_cycle_hours,
        maint_return_eta=eta,
        turnaround_min=spec.turnaround_min,
        capabilities=list(spec.capabilities),
        range_km=spec.range_km,
        cruise_kts=spec.cruise_kts,
        ceiling_ft=spec.ceiling_ft,
        updated_at=start,
    )


def _history(aircraft: Aircraft) -> AircraftHistory:
    return AircraftHistory(
        time=aircraft.updated_at,
        tail_no=aircraft.tail_no,
        status=aircraft.status,
        base_id=aircraft.base_id,
        fuel_kg=aircraft.fuel_kg,
        flight_hours_total=aircraft.flight_hours_total,
        hours_to_next_inspection=aircraft.hours_to_next_inspection,
    )


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)
