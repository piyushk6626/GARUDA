"""Data tool `get_weapon_stock`. Backing: queries/weapons.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.queries.weapons import get_weapon_stock as query_weapon_stock


class WeaponStockItem(BaseModel):
    base_id: str
    weapon_code: str
    category: str
    quantity: int
    reserved_qty: int


class GetWeaponStockInput(BaseModel):
    base_ids: list[str] | None = None
    weapon_codes: list[str] | None = None
    category: str | None = None


class GetWeaponStockOutput(BaseModel):
    total: int
    items: list[WeaponStockItem]


SPEC = ToolSpec(
    name="get_weapon_stock",
    description="Stock and load configs per base. Use before weapon assignment; not for kill probability.",
    input_model=GetWeaponStockInput,
    output_model=GetWeaponStockOutput,
    side_effects="none",
    timeout_s=10,
)

# Retained for the existing skeleton graph import; the tool reads scenario data.
STUB_OUTPUT = GetWeaponStockOutput(
    total=1,
    items=[
        WeaponStockItem(base_id="B1", weapon_code="AGM-1", category="ASM", quantity=12, reserved_qty=2)
    ],
)


async def get_weapon_stock(payload: GetWeaponStockInput, ctx: ToolContext) -> GetWeaponStockOutput:
    """Read stock and its catalog category from a single scenario snapshot."""
    rows = query_weapon_stock(
        ctx.scenario.snapshot(),
        base_ids=payload.base_ids,
        weapon_codes=payload.weapon_codes,
        category=payload.category,
    )
    items = [
        WeaponStockItem(
            base_id=stock.base_id,
            weapon_code=stock.weapon_code,
            category=weapon.category,
            quantity=stock.quantity,
            reserved_qty=stock.reserved_qty,
        )
        for stock, weapon in rows
    ]
    return GetWeaponStockOutput(total=len(items), items=items)
