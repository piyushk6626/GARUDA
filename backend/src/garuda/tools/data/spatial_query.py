"""Data tool `spatial_query`. Backing: queries/gis.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from typing import Literal

from pydantic import BaseModel

from garuda.contracts.tool import ToolContext, ToolSpec
from garuda.tools.stubs import fixed


class GeoRef(BaseModel):
    entity_id: str | None = None
    lat: float | None = None
    lon: float | None = None


class SpatialQueryInput(BaseModel):
    op: Literal["distance_km", "within_radius", "nearest", "intersects", "buffer"]
    a: GeoRef
    b: GeoRef | None = None
    radius_km: float | None = None


class SpatialQueryOutput(BaseModel):
    op: str
    value_km: float | None
    hits: list[str]


SPEC = ToolSpec(
    name="spatial_query",
    description=(
        "Pure geometry: distance, within-radius, nearest, intersects, or buffer between two places "
        "or entities. It does not compute threat-risk maps."
    ),
    input_model=SpatialQueryInput,
    output_model=SpatialQueryOutput,
    side_effects="none",
    timeout_s=10,
)

STUB_OUTPUT = SpatialQueryOutput(op="distance_km", value_km=120.0, hits=["B1"])


async def spatial_query(payload: SpatialQueryInput, ctx: ToolContext) -> SpatialQueryOutput:
    """Skeleton: same output for every input until the query is wired."""
    _ = payload, ctx
    return fixed(STUB_OUTPUT)
