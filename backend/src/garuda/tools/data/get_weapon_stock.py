"""Data tool `get_weapon_stock`. Backing: NEW queries/weapons.py. See docs/02_llm_harness/data_query_tools.md."""

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec


class GetWeaponStockInput(BaseModel):
    pass


class GetWeaponStockOutput(BaseModel):
    pass


SPEC = ToolSpec(
    name="get_weapon_stock",
    description="TODO(phase1): state when to use and not use.",
    input_model=GetWeaponStockInput,
    output_model=GetWeaponStockOutput,
    side_effects="none",
    timeout_s=10,
)


async def get_weapon_stock(payload: GetWeaponStockInput, ctx: ToolContext) -> GetWeaponStockOutput:
    raise NotImplementedError
