"""Read-only stock queries over the scenario and its weapon catalog."""

from typing import get_args

from garuda.contracts.tool import ToolInputError
from garuda.schemas.mission import GeneratedScenario
from garuda.schemas.weapon import WeaponCategory, WeaponStock, WeaponType


def get_weapon_stock(
    scenario: GeneratedScenario,
    *,
    base_ids: list[str] | None = None,
    weapon_codes: list[str] | None = None,
    category: str | None = None,
) -> list[tuple[WeaponStock, WeaponType]]:
    """Join stock with catalog metadata, including zero and fully reserved stock.

    None omits a filter; an empty list matches no rows. Values within a list
    are alternatives, and filters intersect. Unknown filter values and missing
    metadata raise actionable errors instead of returning incomplete data.
    """
    bases = None if base_ids is None else set(base_ids)
    codes = None if weapon_codes is None else set(weapon_codes)
    weapons = {weapon.weapon_code: weapon for weapon in scenario.weapons}
    valid_bases = {base.base_id for base in scenario.bases}
    valid_categories = set(get_args(WeaponCategory))

    if bases is not None:
        _reject_unknown("base_ids", bases, valid_bases)
    if codes is not None:
        _reject_unknown("weapon_codes", codes, set(weapons))
    if category is not None:
        _reject_unknown("category", {category}, valid_categories)

    rows: list[tuple[WeaponStock, WeaponType]] = []
    for stock in sorted(scenario.stocks, key=lambda stock: (stock.base_id, stock.weapon_code)):
        if bases is not None and stock.base_id not in bases:
            continue
        if codes is not None and stock.weapon_code not in codes:
            continue
        weapon = weapons.get(stock.weapon_code)
        if weapon is None:
            raise ToolInputError(
                f"Missing weapon metadata for stock {stock.base_id}:{stock.weapon_code}; "
                "add the matching weapon to the scenario catalog."
            )
        if category is None or weapon.category == category:
            rows.append((stock, weapon))
    return rows


def _reject_unknown(field: str, requested: set[str], valid: set[str]) -> None:
    unknown = requested - valid
    if unknown:
        raise ToolInputError(
            f"Unknown {field}: {', '.join(sorted(unknown))}; "
            f"valid: {', '.join(sorted(valid)) or '(none)'}"
        )
