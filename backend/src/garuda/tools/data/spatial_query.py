"""Data tool `spatial_query`. Backing: queries/gis.py (exists). See docs/02_llm_harness/data_query_tools.md."""

from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from garuda.contracts.tool import ToolContext, ToolInputError, ToolSpec
from garuda.queries import gis
from garuda.schemas.gis import GeoRef as QueryGeoRef
from garuda.schemas.gis import SpatialQuery, SpatialResult


class GeoRef(BaseModel):
    entity_id: str | None = None
    lat: float | None = None
    lon: float | None = None


class SpatialQueryInput(BaseModel):
    op: Literal["distance_km", "within_radius", "nearest", "intersects", "buffer"]
    a: GeoRef
    b: GeoRef | None = None
    radius_km: float | None = Field(default=None, gt=0)


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


def _query_ref(ref: GeoRef) -> QueryGeoRef:
    return QueryGeoRef(entity_id=ref.entity_id, lat=ref.lat, lon=ref.lon)


def _value_km(result: SpatialResult) -> float | None:
    if result.distance_km is not None:
        return result.distance_km
    if len(result.hits) == 1:
        return result.hits[0].distance_km
    return None


def _input_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(part) for part in first["loc"])
    message = str(first["msg"])
    if loc:
        return f"{loc}: {message}"
    return message


async def spatial_query(payload: SpatialQueryInput, ctx: ToolContext) -> SpatialQueryOutput:
    """Run one geometry op over bases, features, borders, and targets in the scenario."""
    scenario = ctx.scenario.snapshot()
    try:
        query = SpatialQuery(
            op=payload.op,
            a=_query_ref(payload.a),
            b=_query_ref(payload.b) if payload.b is not None else None,
            radius_km=payload.radius_km,
        )
    except ValidationError as exc:
        raise ToolInputError(_input_error(exc)) from exc
    try:
        result = gis.spatial_query(
            query,
            scenario.bases,
            scenario.features,
            scenario.borders,
            scenario.targets,
        )
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc
    return SpatialQueryOutput(
        op=result.op,
        value_km=_value_km(result),
        hits=[hit.entity_id for hit in result.hits],
    )
