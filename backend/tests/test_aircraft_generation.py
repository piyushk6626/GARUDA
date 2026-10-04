from pathlib import Path

import pytest
from pydantic import ValidationError

from garuda.config import load_config
from garuda.generators.aircraft import (
    OLDER_TYPES,
    OLDER_WEIGHTS,
    MODERN_WEIGHTS,
    allocate_counts,
)
from garuda.generators.missions import build_scenario
from garuda.queries.aircraft import get_aircraft_availability, is_dispatch_ready
from garuda.schemas.aircraft import Aircraft

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
FLEET = {
    "Su-30MKI": 40,
    "Rafale": 24,
    "Tejas": 24,
    "Mirage-2000": 18,
    "Jaguar": 12,
    "C-130J": 8,
    "IL-78": 6,
    "AEW&C": 4,
    "helicopter": 14,
}


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_scenario_is_reproducible_and_in_range() -> None:
    scenario = _scenario()
    assert 40 <= len(scenario.missions) <= 60
    assert scenario.missions[0].mission_id == "M0001"
    assert 14 <= len(scenario.threats) <= 30
    assert "SAR" in {mission.type for mission in scenario.missions}
    assert [m.model_dump(mode="json") for m in scenario.missions] == [
        m.model_dump(mode="json") for m in _scenario().missions
    ]


def test_fleet_is_reproducible_and_complete() -> None:
    first = _scenario()
    second = _scenario()
    other = _scenario(7)
    assert [airframe.model_dump(mode="json") for airframe in first.aircraft] == [
        airframe.model_dump(mode="json") for airframe in second.aircraft
    ]
    assert [airframe.fuel_kg for airframe in first.aircraft] != [
        airframe.fuel_kg for airframe in other.aircraft
    ]
    assert len(first.aircraft) == 150
    assert len(first.aircraft_history) == 153
    assert {airframe.type for airframe in first.aircraft} == set(FLEET)
    for name, count in FLEET.items():
        rows = [airframe for airframe in first.aircraft if airframe.type == name]
        assert len(rows) == count
        weights = OLDER_WEIGHTS if name in OLDER_TYPES else MODERN_WEIGHTS
        fmc, pmc, nmc = allocate_counts(count, weights)
        assert sum(airframe.status in {"FMC", "IN_MISSION"} for airframe in rows) == fmc
        assert sum(airframe.status == "PMC" for airframe in rows) == pmc
        assert sum(airframe.status in {"NMC", "AOG"} for airframe in rows) == nmc


def test_readiness_rules_and_home_base() -> None:
    scenario = _scenario()
    homes = {squadron.sqn_id: squadron.home_base_id for squadron in scenario.squadrons}
    moved = 0
    for airframe in scenario.aircraft:
        assert airframe.fuel_kg <= airframe.fuel_capacity_kg
        assert 0 <= airframe.hours_to_next_inspection <= airframe.inspection_cycle_hours
        if airframe.status == "FMC":
            assert airframe.status_reason is None
            assert airframe.maint_return_eta is None
        if airframe.status == "NMC":
            assert airframe.status_reason in {"radar_fault", "scheduled_inspection"}
            assert airframe.maint_return_eta is not None
            assert airframe.maint_return_eta > airframe.updated_at
        if airframe.base_id != homes[airframe.sqn_id]:
            moved += 1
    assert moved > 0
    sample = next(airframe for airframe in scenario.aircraft if airframe.tail_no == "SU-A0001")
    assert sample.type == "Su-30MKI"
    assert "BVR" in sample.capabilities


def test_nmc_event_is_history_only_and_extra_states_exist() -> None:
    scenario = _scenario()
    statuses = {airframe.status for airframe in scenario.aircraft}
    assert {"IN_MISSION", "AOG"} <= statuses
    extra = scenario.aircraft_history[len(scenario.aircraft) :]
    assert len(extra) == 3
    assert all(row.status == "NMC" and row.time > scenario.aircraft[0].updated_at for row in extra)


def test_availability_filters_require_every_capability() -> None:
    scenario = _scenario()
    b1 = get_aircraft_availability(scenario.aircraft, base_id="B1", status="FMC", limit=50)
    assert b1
    assert all(airframe.base_id == "B1" and airframe.status == "FMC" for airframe in b1)
    assert b1 == sorted(b1, key=lambda airframe: airframe.tail_no)
    both = get_aircraft_availability(scenario.aircraft, capabilities=["BVR", "AAR_RECEIVER"])
    assert both
    assert all({"BVR", "AAR_RECEIVER"} <= set(airframe.capabilities) for airframe in both)
    ready = [airframe for airframe in scenario.aircraft if is_dispatch_ready(airframe)]
    assert ready
    assert all(airframe.status == "FMC" for airframe in ready)


def test_invalid_fuel_and_fmc_fault_are_rejected() -> None:
    airframe = _scenario().aircraft[0]
    with pytest.raises(ValidationError):
        Aircraft.model_validate({**airframe.model_dump(), "fuel_kg": airframe.fuel_capacity_kg + 1})
    with pytest.raises(ValidationError):
        Aircraft.model_validate({**airframe.model_dump(), "status": "FMC", "status_reason": "radar_fault"})
