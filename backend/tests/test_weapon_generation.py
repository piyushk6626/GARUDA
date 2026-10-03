import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from garuda.config import load_config
from garuda.generators.missions import build_scenario
from garuda.generators.weapons import (
    CLASS_HARDNESS,
    MOUNTING_TYPES,
    external_limit_kg,
    range_penalty_pct,
    station_limit_kg,
    stock_band,
)
from garuda.schemas.weapon import TARGET_CLASSES, WeaponStock
from garuda.storage.kafka_publish import STOCK_TOPIC, stock_event

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"
STRIKE_CATEGORIES = {"ASM", "LGB", "UNGUIDED", "ANTI_RADIATION", "ANTI_SHIP"}
_TOKEN = re.compile(r"^([A-Z0-9-]+)x([1-9][0-9]*)$")
_SAMPLE_STATIONS = {
    "S3": ["LGB-500x2"],
    "S5": ["ASM-STANDOFFx1"],
    "S1": ["AAM-WVRx1"],
    "S12": ["AAM-WVRx1"],
}


def _scenario(seed: int = 42):
    return build_scenario(load_config(CONFIG_PATH), seed)


def test_fixed_seed_is_reproducible() -> None:
    first = _scenario()
    second = _scenario()
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_catalog_covers_every_category() -> None:
    weapons = _scenario().weapons
    assert len(weapons) == 14
    assert len({weapon.weapon_code for weapon in weapons}) == 14
    assert {weapon.category for weapon in weapons} == {
        "AAM_BVR",
        "AAM_WVR",
        "ASM",
        "LGB",
        "UNGUIDED",
        "ANTI_RADIATION",
        "ANTI_SHIP",
        "PODS",
    }
    for weapon in weapons:
        assert set(weapon.pk_by_target_class) == set(TARGET_CLASSES)
        assert all(0.3 <= pk <= 0.9 for pk in weapon.pk_by_target_class.values())


def test_hardness_lowers_pk_for_strike_weapons() -> None:
    hardest_first = sorted(CLASS_HARDNESS, key=CLASS_HARDNESS.__getitem__, reverse=True)
    strike = [weapon for weapon in _scenario().weapons if weapon.category in STRIKE_CATEGORIES]
    assert strike
    for weapon in strike:
        pk = weapon.pk_by_target_class
        for harder, softer in zip(hardest_first, hardest_first[1:]):
            assert pk[harder] <= pk[softer]


def test_stock_follows_base_and_category_bands() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    by_code = {weapon.weapon_code: weapon for weapon in scenario.weapons}
    expected_bases = {f"B{index}" for index in range(1, config.bases + 1)}
    pairs = {(stock.base_id, stock.weapon_code) for stock in scenario.stocks}

    assert len(scenario.stocks) == config.bases * len(scenario.weapons)
    assert pairs == {(base_id, weapon.weapon_code) for base_id in expected_bases for weapon in scenario.weapons}
    for stock in scenario.stocks:
        low, high = stock_band(by_code[stock.weapon_code].category)
        assert low <= stock.quantity <= high
        assert stock.reserved_qty == 0
        assert stock.updated_at == config.start
        assert stock.stock_id == f"{stock.base_id}:{stock.weapon_code}"


def test_load_configs_respect_weight_and_compatibility() -> None:
    config = load_config(CONFIG_PATH)
    scenario = _scenario()
    weights = {weapon.weapon_code: weapon.weight_kg for weapon in scenario.weapons}
    allowed = {
        (row.aircraft_type, row.weapon_code): set(row.allowed_stations)
        for row in scenario.compatibilities
    }
    by_type: dict[str, int] = {}
    for load in scenario.load_configs:
        by_type[load.aircraft_type] = by_type.get(load.aircraft_type, 0) + 1
        total = 0.0
        for station, tokens in load.stations.items():
            station_weight = 0.0
            for token in tokens:
                match = _TOKEN.fullmatch(token)
                assert match is not None
                weapon_code, count = match.group(1), int(match.group(2))
                assert station in allowed[(load.aircraft_type, weapon_code)]
                station_weight += weights[weapon_code] * count
            assert station_weight <= station_limit_kg(load.aircraft_type, station)
            total += station_weight
        assert load.total_weight_kg == total
        assert total <= external_limit_kg(load.aircraft_type)
        assert load.range_penalty_pct == range_penalty_pct(total, external_limit_kg(load.aircraft_type))

    assert set(by_type) == set(MOUNTING_TYPES)
    assert all(3 <= count <= 6 for count in by_type.values())
    support = set(config.fleet) - MOUNTING_TYPES
    assert support == {"C-130J", "IL-78", "AEW"}
    assert all(row.aircraft_type not in support for row in scenario.compatibilities)
    strike = next(load for load in scenario.load_configs if load.config_id == "SU30-STRIKE-1")
    assert strike.aircraft_type == "Su-30MKI"
    assert strike.role == "STRIKE"
    assert strike.stations == _SAMPLE_STATIONS


def test_negative_quantity_is_rejected() -> None:
    moment = load_config(CONFIG_PATH).start
    with pytest.raises(ValidationError):
        WeaponStock(
            base_id="B1",
            weapon_code="AAM-WVR",
            quantity=-1,
            reserved_qty=0,
            updated_at=moment,
        )


def test_stock_event_id_is_stable() -> None:
    stock = _scenario().stocks[0]
    first = stock_event(stock)
    second = stock_event(stock)
    assert STOCK_TOPIC == "garuda.weapons.stock"
    assert first["event_id"] == second["event_id"]
    assert first["entity_id"] == stock.stock_id
    assert first["type"] == "weapons.stock.updated"
    assert first["version"] == 1
    assert set(first) == {"event_id", "type", "entity_id", "ts", "version", "payload"}
