# GARUDA
Global Air Resource & Unified Decision Architecture

- Problem Statement: Complexity in planning and dynamically retasking air operations in a contested and rapidly changing operational environment. Information on aircraft availability, crew status, weapon loads,airspace, weather, threats and mission priorities is generated across multiple systems but is not always available through a common, real-time decision-support framework, leading to increased planning timelines and sub-optimal allocation of scarce airpower resources.
- Technology Opportunity: Al-enabled decision-support, multi - source data fusion, predictive analytics and optimisation algorithms.

## Architecture
Three layers: (1) multi-source data layer (real feeds plus synthetic generators; Postgres/PostGIS, TimescaleDB, Neo4j, Redis, Redpanda, MinIO), (2) LLM harness (LangGraph + FastAPI, typed tools, human approval), (3) optimization and predictive layer (MILP, CP-SAT, VRP, route planning, ML, Monte Carlo). All military data is synthetic and unclassified.

## Design docs
- Overview: [architecture](docs/00_overview/architecture.md), [ontology](docs/00_overview/ontology.md), [tech stack options](docs/00_overview/tech_stack_options.md), [scenario](docs/00_overview/scenario_definition.md)
- Data layer (`docs/01_data_layer/`): [aircraft](docs/01_data_layer/aircraft_availability.md), [crew](docs/01_data_layer/crew_status.md), [weapons](docs/01_data_layer/weapon_loads.md), [airspace](docs/01_data_layer/airspace.md), [weather](docs/01_data_layer/weather.md), [threats](docs/01_data_layer/threats.md), [missions](docs/01_data_layer/mission_priorities.md), [GIS](docs/01_data_layer/gis_terrain_bases.md), [ADS-B](docs/01_data_layer/air_traffic_adsb.md), [synthetic generation](docs/01_data_layer/synthetic_data_generation.md), [storage](docs/01_data_layer/storage_architecture.md)
- Isolated airspace operational stack: [infra/airspace](infra/airspace/README.md)
- Isolated crew-status operational stack: [infra/crew](infra/crew/README.md)
- LLM harness (`docs/02_llm_harness/`): [overview](docs/02_llm_harness/harness_overview.md), [tool catalog](docs/02_llm_harness/tool_catalog.md), [data tools](docs/02_llm_harness/data_query_tools.md), [retrieval](docs/02_llm_harness/retrieval_and_context.md), [guardrails](docs/02_llm_harness/guardrails_and_hitl.md), [API](docs/02_llm_harness/api_design.md)
- Optimization and ML: [index](docs/03_optimization_layer/README.md)
