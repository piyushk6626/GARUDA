from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FeatureKind = Literal["road", "city", "river", "water"]
BorderKind = Literal["international", "coastline", "admin"]
SpatialOp = Literal["distance_km", "within_radius", "nearest", "intersects", "buffer"]


class BaseRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str
    name: str
    icao: str
    lat: float
    lon: float
    elevation_ft: int
    runway_count: int = Field(ge=1)
    runway_length_m: int = Field(gt=0)
    shelter_count: int = Field(ge=0)
    fuel_capacity_l: int = Field(ge=0)
    ground_crew_teams: int = Field(ge=0)
    max_sorties_per_hour: float = Field(gt=0)


class GeoFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature_id: str
    kind: FeatureKind
    name: str
    wkt: str


class BorderFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    border_id: str
    name: str
    kind: BorderKind
    wkt: str


class BaseTargetDistance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str
    target_id: str
    distance_km: float = Field(ge=0)


class TerrainMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    cell_km: float = Field(gt=0)
    rows: int = Field(gt=0)
    cols: int = Field(gt=0)
    elev_min_m: float
    elev_max_m: float


class GeoRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str | None = None
    lat: float | None = None
    lon: float | None = None


class SpatialQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: SpatialOp
    a: GeoRef
    b: GeoRef | None = None
    radius_km: float | None = Field(default=None, gt=0)


class SpatialHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str
    kind: str
    lat: float
    lon: float
    distance_km: float | None = None


class SpatialResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: SpatialOp
    distance_km: float | None = None
    hits: list[SpatialHit] = Field(default_factory=list)
    wkt: str | None = None


class DistanceMatrixQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_ids: list[str] = Field(min_length=1)
    target_ids: list[str] = Field(min_length=1)


class DistanceMatrixResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_ids: list[str]
    target_ids: list[str]
    kilometres: list[list[float]]
