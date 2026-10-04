"""Data tool `get_weapon_stock`. Backing: NEW queries/weapons.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


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

STUB_OUTPUT = GetWeaponStockOutput(
    total=1,
    items=[
        WeaponStockItem(base_id="B1", weapon_code="AGM-1", category="ASM", quantity=12, reserved_qty=2)
    ],
)


async def get_weapon_stock(payload: GetWeaponStockInput, ctx: ToolContext) -> GetWeaponStockOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
