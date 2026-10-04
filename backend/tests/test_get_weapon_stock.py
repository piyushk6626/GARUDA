import asyncio
from datetime import datetime
from pathlib import Path
from typing import get_args

import pytest

from garuda.config import load_config
from garuda.contracts.tool import ToolContext, ToolInputError
from garuda.generators.missions import build_scenario
from garuda.queries.weapons import get_weapon_stock as query_weapon_stock
from garuda.schemas.mission import GeneratedScenario
from garuda.schemas.weapon import WeaponCategory
from garuda.tools.data.get_weapon_stock import GetWeaponStockInput, GetWeaponStockOutput, get_weapon_stock

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "scenario.yaml"


class _Source:
    def __init__(self, scenario: GeneratedScenario) -> None:
        self.scenario = scenario
        self.snapshot_calls = 0

    def snapshot(self) -> GeneratedScenario:
        self.snapshot_calls += 1
        return self.scenario

    def base_ids(self) -> list[str]:
        return [base.base_id for base in self.scenario.bases]

    def data_as_of(self) -> datetime:
        return load_config(CONFIG_PATH).start


@pytest.fixture(scope="module")
def generated_scenario() -> GeneratedScenario:
    return build_scenario(load_config(CONFIG_PATH), 42)


@pytest.fixture
def source(generated_scenario: GeneratedScenario) -> _Source:
    return _Source(
        generated_scenario.model_copy(
            update={
                "stocks": [stock.model_copy(deep=True) for stock in generated_scenario.stocks],
                "weapons": [weapon.model_copy(deep=True) for weapon in generated_scenario.weapons],
            }
        )
    )


def _query(source: _Source, **filters) -> GetWeaponStockOutput:
    ctx = ToolContext(
        scenario=source,
        plans=None,
        models=None,
        providers=None,
        scenario_time=source.data_as_of(),
    )
    return asyncio.run(get_weapon_stock(GetWeaponStockInput(**filters), ctx))


def test_returns_scenario_stock_with_catalog_categories(source: _Source) -> None:
    result = _query(source)
    categories = {weapon.weapon_code: weapon.category for weapon in source.scenario.weapons}
    expected = [
        {
            "base_id": stock.base_id,
            "weapon_code": stock.weapon_code,
            "category": categories[stock.weapon_code],
            "quantity": stock.quantity,
            "reserved_qty": stock.reserved_qty,
        }
        for stock in sorted(source.scenario.stocks, key=lambda stock: (stock.base_id, stock.weapon_code))
    ]
    assert result.model_dump() == {"total": len(expected), "items": expected}
    assert source.snapshot_calls == 1


@pytest.mark.parametrize(
    "filters",
    [
        {"base_ids": ["B2", "B1"]},
        {"weapon_codes": ["LGB-500", "ASM-MED"]},
        {"category": "ASM"},
        {"base_ids": ["B2", "B1"], "weapon_codes": ["LGB-500", "ASM-MED"]},
        {"base_ids": ["B1"], "category": "ASM"},
        {"weapon_codes": ["ASM-MED", "LGB-500"], "category": "LGB"},
        {"base_ids": ["B1"], "weapon_codes": ["ASM-MED", "LGB-500"], "category": "ASM"},
    ],
)
def test_filters_match_within_lists_and_intersect_across_fields(source: _Source, filters: dict) -> None:
    result = _query(source, **filters)
    categories = {weapon.weapon_code: weapon.category for weapon in source.scenario.weapons}
    expected = sorted(
        (stock.base_id, stock.weapon_code)
        for stock in source.scenario.stocks
        if ("base_ids" not in filters or stock.base_id in filters["base_ids"])
        and ("weapon_codes" not in filters or stock.weapon_code in filters["weapon_codes"])
        and ("category" not in filters or categories[stock.weapon_code] == filters["category"])
    )
    assert expected
    assert [(item.base_id, item.weapon_code) for item in result.items] == expected
    assert result.total == len(expected)


@pytest.mark.parametrize("category", get_args(WeaponCategory))
def test_supports_every_existing_category(source: _Source, category: str) -> None:
    result = _query(source, category=category)
    codes = {weapon.weapon_code for weapon in source.scenario.weapons if weapon.category == category}
    assert result.total == sum(stock.weapon_code in codes for stock in source.scenario.stocks)
    assert result.items
    assert all(item.category == category and item.weapon_code in codes for item in result.items)


def test_zero_stock_and_partial_and_full_reservations_are_preserved(source: _Source) -> None:
    base_id = source.scenario.stocks[0].base_id
    stocks = [stock for stock in source.scenario.stocks if stock.base_id == base_id][:3]
    stocks[0].quantity, stocks[0].reserved_qty = 0, 0
    stocks[1].quantity, stocks[1].reserved_qty = 12, 2
    stocks[2].quantity, stocks[2].reserved_qty = 7, 7
    result = _query(source, base_ids=[stocks[0].base_id], weapon_codes=[stock.weapon_code for stock in stocks])
    assert result.total == 3
    assert {item.weapon_code: (item.quantity, item.reserved_qty) for item in result.items} == {
        stock.weapon_code: (stock.quantity, stock.reserved_qty) for stock in stocks
    }


def test_valid_filters_with_no_intersection_return_empty_result(source: _Source) -> None:
    result = _query(source, weapon_codes=["LGB-500"], category="ASM")
    assert result.model_dump() == {"total": 0, "items": []}


def test_unknown_base_is_an_actionable_error(source: _Source) -> None:
    with pytest.raises(ToolInputError, match="Unknown base_ids: MISSING; valid: B1"):
        _query(source, base_ids=["B1", "MISSING"])


@pytest.mark.parametrize("filters", [{"base_ids": []}, {"weapon_codes": []}, {"base_ids": [], "weapon_codes": []}])
def test_empty_lists_match_no_stock(source: _Source, filters: dict) -> None:
    assert _query(source, **filters).model_dump() == {"total": 0, "items": []}


@pytest.mark.parametrize(
    "filters, error",
    [
        ({"weapon_codes": ["ASM-MED", "MISSING"]}, "Unknown weapon_codes: MISSING; valid:"),
        ({"category": "MISSING"}, "Unknown category: MISSING; valid:"),
        ({"category": "asm"}, "Unknown category: asm; valid:"),
        ({"base_ids": [], "weapon_codes": ["MISSING"]}, "Unknown weapon_codes: MISSING; valid:"),
    ],
)
def test_unknown_codes_and_categories_are_actionable_errors(source: _Source, filters: dict, error: str) -> None:
    with pytest.raises(ToolInputError, match=error):
        _query(source, **filters)


def test_known_category_with_no_catalog_entries_matches_no_stock(source: _Source) -> None:
    source.scenario.weapons = [weapon for weapon in source.scenario.weapons if weapon.category != "ASM"]
    codes = {weapon.weapon_code for weapon in source.scenario.weapons}
    source.scenario.stocks = [stock for stock in source.scenario.stocks if stock.weapon_code in codes]
    assert _query(source, category="ASM").model_dump() == {"total": 0, "items": []}


def test_known_code_without_stock_matches_no_stock(source: _Source) -> None:
    source.scenario.stocks = [stock for stock in source.scenario.stocks if stock.weapon_code != "ASM-MED"]
    assert _query(source, weapon_codes=["ASM-MED"]).model_dump() == {"total": 0, "items": []}


@pytest.mark.parametrize("category", [None, "ASM"])
def test_missing_metadata_raises_instead_of_inventing_or_omitting_category(
    source: _Source, category: str | None
) -> None:
    stock = source.scenario.stocks[0]
    source.scenario.weapons = [weapon for weapon in source.scenario.weapons if weapon.weapon_code != stock.weapon_code]
    with pytest.raises(ToolInputError, match=f"Missing weapon metadata for stock {stock.base_id}:{stock.weapon_code}"):
        _query(source, base_ids=[stock.base_id], category=category)


def test_stock_outside_requested_bases_does_not_require_metadata(source: _Source) -> None:
    stock = source.scenario.stocks[0]
    source.scenario.weapons = [weapon for weapon in source.scenario.weapons if weapon.weapon_code != stock.weapon_code]
    source.scenario.stocks = [stock]
    other_base = next(base.base_id for base in source.scenario.bases if base.base_id != stock.base_id)
    assert _query(source, base_ids=[other_base]).model_dump() == {"total": 0, "items": []}


def test_category_comes_from_metadata_even_when_weapon_code_suggests_another_category(source: _Source) -> None:
    weapon = next(weapon for weapon in source.scenario.weapons if weapon.weapon_code == "ASM-MED")
    weapon.category = "PODS"
    result = _query(source, weapon_codes=[weapon.weapon_code], category="PODS")
    assert result.total == len(source.scenario.bases)
    assert all(item.category == "PODS" for item in result.items)
    assert _query(source, weapon_codes=[weapon.weapon_code], category="ASM").total == 0


def test_backing_query_preserves_stock_and_metadata_without_mutating_source(source: _Source) -> None:
    stocks_before = [stock.model_dump() for stock in source.scenario.stocks]
    weapons_before = [weapon.model_dump() for weapon in source.scenario.weapons]
    rows = query_weapon_stock(source.scenario, base_ids=["B1"], weapon_codes=["LGB-500"])
    assert len(rows) == 1
    stock, weapon = rows[0]
    assert (stock.base_id, stock.weapon_code, weapon.category) == ("B1", "LGB-500", "LGB")
    assert [stock.model_dump() for stock in source.scenario.stocks] == stocks_before
    assert [weapon.model_dump() for weapon in source.scenario.weapons] == weapons_before


def test_base_and_catalog_entries_without_stock_do_not_create_rows(source: _Source) -> None:
    source.scenario.stocks = [stock for stock in source.scenario.stocks if stock.base_id != "B1"]
    assert _query(source, base_ids=["B1"]).model_dump() == {"total": 0, "items": []}
    source.scenario.stocks = []
    assert _query(source).model_dump() == {"total": 0, "items": []}


def test_order_is_stable_and_duplicate_filters_do_not_duplicate_stock(source: _Source) -> None:
    expected = _query(source, base_ids=["B1", "B2"], weapon_codes=["LGB-500", "ASM-MED"])
    source.scenario.stocks.reverse()
    source.scenario.weapons.reverse()
    result = _query(source, base_ids=["B2", "B1", "B2"], weapon_codes=["ASM-MED", "LGB-500", "ASM-MED"])
    assert result == expected


def test_reads_current_snapshot_without_mutating_stock_or_metadata(source: _Source) -> None:
    before = source.scenario.model_dump()
    first = _query(source)
    assert source.scenario.model_dump() == before
    first.items[0].quantity = 99999
    first.items.clear()
    assert source.scenario.model_dump() == before
    stock = source.scenario.stocks[0]
    stock.quantity, stock.reserved_qty = 9, 4
    second = _query(source, base_ids=[stock.base_id], weapon_codes=[stock.weapon_code])
    assert second.total == 1
    assert (second.items[0].quantity, second.items[0].reserved_qty) == (9, 4)
