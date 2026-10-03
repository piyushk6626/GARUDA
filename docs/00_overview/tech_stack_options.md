# Tech Stack Options

Chosen option marked **(chosen)**.

## Graph / ontology store
| Option | Pros | Cons |
|---|---|---|
| Neo4j **(chosen)** | Mature, Cypher, visual browser | Extra service; licensing for clustering |
| Postgres + Apache AGE | One DB less | Younger tooling |
| Postgres recursive CTEs only | Simplest | Awkward multi-hop queries |

## Streaming
| Option | Pros | Cons |
|---|---|---|
| Redpanda **(chosen)** | Kafka API, single binary, light | Less ecosystem than Kafka |
| Apache Kafka | Standard | Heavy for hackathon |
| Redis Streams | Already have Redis | Weaker retention/replay |

## Time series
TimescaleDB **(chosen)** (same Postgres engine, hypertables, continuous aggregates) vs InfluxDB vs plain partitioned Postgres.

## Agent harness
| Option | Pros | Cons |
|---|---|---|
| LangGraph **(chosen)** | Stateful graph, checkpoints, human-in-loop interrupts | LangChain dependency weight |
| Custom tool loop | Minimal, transparent | Rebuild checkpoints, HITL |
| MCP server | Any client can use tools | Less control of flow; add later as an adapter over the same tools |

Tools are plain Python functions with Pydantic schemas so they can be wrapped by LangGraph or MCP.

## Solvers
| Option | Use | Notes |
|---|---|---|
| OR-Tools CP-SAT **(chosen)** | Scheduling, assignment | Free, strong |
| OR-Tools routing | VRP | Free |
| PuLP + HiGHS / CBC **(chosen)** | LP/MILP | Free |
| Gurobi/CPLEX | Large MILP | Commercial; optional upgrade |
| pymoo | NSGA-II multi-objective | Stretch |

## ML
XGBoost/LightGBM, scikit-learn, lifelines, SHAP, Gymnasium + Stable-Baselines3 (stretch).

## LLM
Cloud API behind an adapter interface `LLMClient` (OpenAI/Anthropic); swap to vLLM/Ollama for air-gapped use. Note: for real deployment, classified data must not reach cloud APIs.

## Geospatial
PostGIS, Shapely, rasterio (SRTM), GeoPandas, H3 or a regular lat/lon grid for route cost surfaces.
