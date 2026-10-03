# Tool Catalog

All tools: async Python function taking one Pydantic model, returning one Pydantic model (RORO). Registered once in a registry and adapted to LangGraph `StructuredTool` (and optionally MCP).

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    side_effects: Literal["none","draft","commit"]
    timeout_s: int
```

## Data tools (side_effects = none)
| Tool | Purpose | Backing store | Doc |
|---|---|---|---|
| `get_aircraft_availability` | Filter aircraft by base/type/status/time | Postgres/Redis | data_query_tools |
| `get_crew_status` | Crew by qualification, fatigue, availability | Postgres | data_query_tools |
| `get_weapon_stock` | Stock and load configs per base | Postgres/Redis | data_query_tools |
| `get_airspace` | Zones active along route/time/alt | PostGIS | data_query_tools |
| `get_weather_along_route` | Weather and flyability for route/time | Timescale/Redis | data_query_tools |
| `get_threats_in_area` | Threats within polygon/radius | Redis geo/PostGIS | data_query_tools |
| `get_mission_requests` | Pending/planned missions | Postgres | data_query_tools |
| `get_civil_traffic` | ADS-B tracks in area | Redis/Timescale | data_query_tools |
| `query_ontology` | Templated Cypher queries | Neo4j | data_query_tools |
| `spatial_query` | Distances, buffers, intersections | PostGIS | data_query_tools |
| `search_doctrine` | RAG over SOP/doctrine docs | pgvector | retrieval_and_context |

## Optimization tools
| Tool | Algorithm doc | side_effects |
|---|---|---|
| `assign_missions` | milp_mission_aircraft_assignment | draft |
| `schedule_crew` | cpsat_crew_scheduling | draft |
| `assign_weapons_to_targets` | weapon_target_assignment | draft |
| `compute_sortie_capacity` | lp_sortie_generation_rate | none |
| `plan_route` | threat_aware_route_planning | none |
| `schedule_tanker_aar` | vrp_tanker_aar_scheduling | draft |
| `deconflict_airspace` | cpsat_airspace_deconfliction | draft |
| `retask_rolling_horizon` | rolling_horizon_dynamic_retasking | draft |
| `pareto_tradeoffs` | multi_objective_tradeoffs | none |

## Predictive tools (side_effects = none)
| Tool | Algorithm doc |
|---|---|
| `forecast_availability` | aircraft_availability_forecast |
| `predict_component_rul` | component_failure_survival |
| `assess_crew_fatigue` | crew_fatigue_model |
| `predict_weather_impact` | weather_impact_prediction |
| `detect_threat_anomalies` | threat_anomaly_detection |
| `predict_mission_success` | mission_success_probability |
| `evaluate_plan_monte_carlo` | monte_carlo_plan_evaluation |
| `rl_retask` (stretch) | rl_retasking_agent |

## Commit tool
`commit_plan(plan_id)`: side_effects = commit; executable only after approval token from the Approval node (not callable by the LLM directly).

## Common output envelope
```python
class ToolResult(BaseModel):
    ok: bool
    data: dict | None
    warnings: list[str] = []
    data_as_of: datetime
    explanation: str | None = None   # solver rationale
    error: str | None = None
```

## Conventions
- Tool names snake_case verbs; descriptions state when to use and not use.
- Every solver tool accepts `scenario_time`, `horizon_hours`, `time_limit_s`.
- Plans are referenced by `plan_id`; heavy payloads are stored server-side and only summaries go to the LLM.
