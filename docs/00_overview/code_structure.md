# Tool Layer Code Structure

Reference layout for every tool in `docs/02_llm_harness/tool_catalog.md` and `docs/03_optimization_layer/`. Everything lives in `backend/src/garuda/`. **Status: skeleton only.** Contracts and interfaces exist; all tool bodies raise `NotImplementedError` and are marked `TODO(phase1)`.

## Principles
- One tool, one package. An owner touches only their package, their test file, their rules in `validation/rules.py`, and one line in `tools/catalog.py`.
- No tool imports another tool. Tools connect only through frozen `contracts/`, provider protocols (`providers/protocols.py`), and `plan_id`s in a `PlanStore`.
- Tools are `async def tool(payload: Input, ctx: ToolContext) -> Output` (RORO). CPU-heavy solves go through `tools.executor.run_cpu`.
- `tools.envelope.run_tool` wraps outputs in `ToolResult`; tool code never builds the envelope.

## Layout
```
garuda/
  contracts/      geo, plan, events, results, solve, tool   (FROZEN)
  context/        ports.py (ScenarioSource, PlanStore, ModelStore, Clock), memory.py, factory.py
  providers/      protocols.py (FROZEN), baseline.py, tool_backed.py (phase 2)
  tools/          registry, envelope, executor, catalog, shaping
    data/         11 data tools (one file each)
    commit/       commit_plan (approval token only)
    adapters/     langgraph.py, mcp.py (phase 2)
  optimization/   common/ + 9 tool packages
  ml/             common/ + 6 tool packages
  sim/            kernel.py (FROZEN signatures), evaluate_plan_monte_carlo/, rl_retask/ (stretch)
  validation/     plan_validator.py, rules.py
```

## Tool package template
Optimization and sim: `__init__.py, schemas.py, problem.py, model.py, baseline.py, explain.py, tool.py`.
ML: `__init__.py, schemas.py, features.py, train.py, predict.py, explain.py, tool.py, __main__.py` (`python -m garuda.ml.<tool>.train`).
`tool.py` holds `SPEC` (a `ToolSpec`) and the tool function; `schemas.py` is copied from the algorithm doc.

## Tools by package
| Tool | Package | Side effects | Doc |
|---|---|---|---|
| assign_missions | optimization/ | draft | milp_mission_aircraft_assignment |
| schedule_crew | optimization/ | draft | cpsat_crew_scheduling |
| assign_weapons_to_targets | optimization/ | draft | weapon_target_assignment |
| compute_sortie_capacity | optimization/ | none | lp_sortie_generation_rate |
| plan_route | optimization/ | none | threat_aware_route_planning |
| schedule_tanker_aar | optimization/ | draft | vrp_tanker_aar_scheduling |
| deconflict_airspace | optimization/ | draft | cpsat_airspace_deconfliction |
| retask_rolling_horizon | optimization/ | draft | rolling_horizon_dynamic_retasking |
| pareto_tradeoffs | optimization/ | none | multi_objective_tradeoffs |
| forecast_availability | ml/ | none | aircraft_availability_forecast |
| predict_component_rul | ml/ | none | component_failure_survival |
| assess_crew_fatigue | ml/ | none | crew_fatigue_model |
| predict_weather_impact | ml/ | none | weather_impact_prediction |
| detect_threat_anomalies | ml/ | none | threat_anomaly_detection |
| predict_mission_success | ml/ | none | mission_success_probability |
| evaluate_plan_monte_carlo | sim/ | none | monte_carlo_plan_evaluation |
| rl_retask (stretch) | sim/ | none | rl_retasking_agent |
| commit_plan | tools/commit/ | commit | guardrails_and_hitl |

Data tools (`tools/data/`, side_effects none): get_aircraft_availability, get_crew_status, get_weapon_stock, get_airspace, get_weather_along_route, get_threats_in_area, get_mission_requests, get_civil_traffic, query_ontology, spatial_query, search_doctrine. Existing query code is wrapped (`queries/aircraft|crew|airspace|weather|adsb|gis.py`); new query modules needed: `queries/weapons.py`, `threats.py`, `missions.py`, `ontology.py`, `doctrine.py` (+ `ingest/doctrine.py`).

## Providers (decoupling points)
| Provider | Baseline | Replaced by |
|---|---|---|
| AvailabilityProvider.p_fmc | status + maintenance ETA | forecast_availability |
| ComponentRiskProvider.aircraft_risk | hours to next inspection | predict_component_rul |
| WeatherGoProvider.p_go | flyability threshold | predict_weather_impact |
| RouteProvider.route | great-circle vs risk raster | plan_route |
| CapacityProvider.cap | FMC count x 60 / turnaround | compute_sortie_capacity |
| PkProvider.pk | weapon Pk x hardness | predict_mission_success |
| FatigueProvider.fatigue / phi | Crew.fatigue_score + constant | assess_crew_fatigue |
| ThreatRiskProvider.raster | risk raster from threats | anomaly-updated raster |

## Shared extension points
- `optimization/assign_missions/problem.py`: `AssignmentProblem`, `ModelExtension`. `retask_rolling_horizon` and `pareto_tradeoffs` only add extensions (frozen set + stability penalty; epsilon constraints).
- `sim/kernel.py`: `sample_launch`, `sample_weather`, `sample_engagement`, `simulate`. Used by Monte Carlo, `predict_mission_success` training, and `rl_retask`.
- `validation/plan_validator.py`: `validate_plan(plan, ctx)`; every solver test runs it and compares against `baseline.py`.

## Frozen files
`contracts/`, `providers/protocols.py`, `sim/kernel.py` signatures, `assign_missions/problem.py`. Changes go in a separate contracts-only PR.

## Dependencies
Optional extras in `backend/pyproject.toml`: `opt`, `ml`, `moo`, `rl`, `api`, so parallel branches do not conflict on `uv.lock`.

## Phases
0. Foundation (this skeleton). 1. Tool implementations in parallel. 2. Wiring: tool-backed providers, postgres context, LangGraph adapter, `/v1/tools/{tool_name}`, end-to-end chain test.

## Parallel workstreams
1 data tools; 2 compute_sortie_capacity, assign_missions, plan_route; 3 schedule_crew, assess_crew_fatigue; 4 assign_weapons_to_targets, predict_mission_success; 5 deconflict_airspace, schedule_tanker_aar; 6 retask_rolling_horizon, pareto_tradeoffs; 7 forecast_availability, predict_component_rul, predict_weather_impact, detect_threat_anomalies; 8 sim kernel, evaluate_plan_monte_carlo, rl_retask; 9 commit_plan, adapters, API, provider wiring.

## Phase 1 fixtures (to be built)
See `backend/tests/fixtures/opt/README.md`: toy scenario with known optimum, toy plan JSON, `ctx_toy` and `ctx_seed42` fixtures.
