# Data Query Tools

## Pydantic signatures

```python
class AircraftQuery(BaseModel):
    base_ids: list[str] | None = None
    types: list[str] | None = None
    statuses: list[Literal["FMC","PMC","NMC","AOG","IN_MISSION"]] | None = None
    capabilities: list[str] | None = None
    available_at: datetime | None = None   # uses forecast if in future
    limit: int = Field(50, le=200)

class AircraftQueryResult(BaseModel):
    total: int
    items: list[AircraftState]
    counts_by_status: dict[str, int]
```

```python
class WeatherRouteQuery(BaseModel):
    route: list[tuple[float, float]]    # lat, lon
    start_time: datetime
    speed_kts: float
    altitude_ft: int

class WeatherRouteResult(BaseModel):
    segments: list[WeatherSegment]       # per sample point: time, flyability, ceiling, vis, wind, cb_flag
    worst_flyability: float
    advisories: list[str]
```

```python
class ThreatAreaQuery(BaseModel):
    center: tuple[float, float] | None = None
    radius_km: float | None = None
    polygon: list[tuple[float, float]] | None = None
    min_confidence: float = 0.3
    types: list[str] | None = None
```

Similar models: `CrewQuery(base_ids, role, qualification, max_fatigue, available_from, available_to)`, `WeaponStockQuery(base_ids, weapon_codes, category)`, `AirspaceQuery(route|polygon, altitude_ft, time)`, `MissionQuery(status, types, priority_max, window)`, `TrafficQuery(polygon, since)`.

## query_ontology (templated Cypher)
The LLM does **not** write free Cypher. It selects a template and parameters:
```python
class OntologyQuery(BaseModel):
    template: Literal["aircraft_with_qualified_crew","missions_for_target","assets_at_base","threats_covering_target","plan_assignments"]
    params: dict[str, str | int | float]
```
Templates are parameterized Cypher stored in code; run on a read-only Neo4j user with query timeout.

## spatial_query
```python
class SpatialQuery(BaseModel):
    op: Literal["distance_km","within_radius","nearest","intersects","buffer"]
    a: GeoRef   # point id/coords or entity ref
    b: GeoRef | None = None
    radius_km: float | None = None
```
Mapped to PostGIS functions with `ST_DistanceSphere`, `ST_DWithin` (geography).

## Optional text-to-SQL (guardrails)
If enabled for ad hoc analytics only:
- Separate read-only role `llm_ro`, `SELECT` on whitelisted views only.
- Parse with `sqlglot`; reject non-SELECT, multiple statements, unknown tables; auto `LIMIT 500`; `statement_timeout = 5s`.
- Prefer curated tools; text-to-SQL is fallback.

## Result shaping
Return aggregates + top N rows, `data_as_of`, and truncation flags. Never return > ~4k tokens to the LLM.

## Errors
`ToolResult.ok=False` with a short actionable message (e.g. "unknown base_id B9; valid: B1..B7") so the LLM can self-correct.
