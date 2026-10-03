"""Pure deterministic fictional fleet generation; it performs no I/O."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import yaml

from ...aircraft_config import AircraftScenario
from ...schemas.aircraft import AIRCRAFT_EVENT_NAMESPACE, AircraftState, AircraftStatus, AircraftType

PREFIX = {"Su-30MKI": "SU", "Rafale": "RF", "Tejas": "TJ", "Mirage-2000": "MG", "Jaguar": "JG", "C-130J": "C1", "IL-78": "IL", "AEW&C": "AE", "helicopter": "HL"}


@dataclass(frozen=True)
class FleetSeed:
    types: tuple[AircraftType, ...]
    squadrons: tuple[dict[str, str], ...]
    states: tuple[AircraftState, ...]

    def checksum(self) -> str:
        payload = [state.model_dump(mode="json") for state in self.states]
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_type_catalog(path: str | Path = "config/aircraft_types.yaml") -> dict[str, AircraftType]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {name: AircraftType(type=name, **values) for name, values in raw.items()}


def _counts(total: int, weights: tuple[float, float, float]) -> tuple[int, int, int]:
    exact = [total * weight for weight in weights]
    result = [int(value) for value in exact]
    for index in sorted(range(3), key=lambda item: exact[item] - result[item], reverse=True)[:total - sum(result)]:
        result[index] += 1
    return tuple(result)  # type: ignore[return-value]


def generate_fleet(scenario: AircraftScenario, run_id: str, *, catalog_path: str | Path = "config/aircraft_types.yaml") -> FleetSeed:
    catalog = load_type_catalog(catalog_path)
    generation_rng = np.random.default_rng(np.random.SeedSequence(scenario.seed, spawn_key=(1,)))
    repair_rng = np.random.default_rng(np.random.SeedSequence(scenario.seed, spawn_key=(2,)))
    states: list[AircraftState] = []
    squadrons: list[dict[str, str]] = []
    bases = tuple(item.base_id for item in scenario.bases)
    for type_index, (kind, count) in enumerate(scenario.fleet.items()):
        spec = catalog[kind]
        sqn_id = f"{run_id}-{PREFIX[kind]}-SQN"
        home = bases[type_index % len(bases)]
        squadrons.append({"sqn_id": sqn_id, "home_base_id": home, "type": kind, "name": f"{kind} Fictional Squadron", "role": "synthetic"})
        weights = (0.70, 0.12, 0.18) if kind in ("Mirage-2000", "Jaguar") else (0.75, 0.10, 0.15)
        fmc, pmc, nmc = _counts(count, weights)
        statuses = np.array([AircraftStatus.FMC] * fmc + [AircraftStatus.PMC] * pmc + [AircraftStatus.NMC] * nmc, dtype=object)
        generation_rng.shuffle(statuses)
        for number, status in enumerate(statuses, 1):
            tail_no = f"{run_id.upper()}-{PREFIX[kind]}-{number:03d}"
            since = scenario.start_at - timedelta(hours=float(generation_rng.uniform(1, 72)))
            hours_since = float(generation_rng.uniform(0, spec.inspection_cycle_hours))
            total = float(generation_rng.gamma(4, 1500))
            while total < hours_since:
                total = float(generation_rng.gamma(4, 1500))
            eta = None
            reason = None
            if status is AircraftStatus.NMC:
                reason = "synthetic_fault"
                eta = scenario.start_at + timedelta(hours=float(repair_rng.lognormal(np.log(18), .7)))
            if status is AircraftStatus.PMC:
                reason = "restricted_synthetic"
            event_id = uuid.uuid5(AIRCRAFT_EVENT_NAMESPACE, f"{run_id}:{tail_no}:1")
            states.append(AircraftState(tail_no=tail_no, run_id=run_id, type=kind, sqn_id=sqn_id,
                base_id=bases[(type_index + number - 1) % len(bases)], status=status, status_reason=reason,
                fuel_kg=float(generation_rng.uniform(spec.fuel_capacity_kg * .35, spec.fuel_capacity_kg * .95)),
                flight_hours_total=total, hours_since_inspection=hours_since,
                hours_to_next_inspection=spec.inspection_cycle_hours - hours_since, maint_return_eta=eta,
                capabilities=spec.capabilities, turnaround_min=spec.turnaround_min, status_since=since,
                ready_at=scenario.start_at if status is not AircraftStatus.NMC else eta or scenario.start_at,
                updated_at=scenario.start_at, version=1, last_event_id=event_id))
    return FleetSeed(types=tuple(catalog.values()), squadrons=tuple(squadrons), states=tuple(sorted(states, key=lambda state: state.tail_no)))
