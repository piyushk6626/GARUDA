# Storage Architecture

## Ownership matrix
| Entity | Postgres/PostGIS | TimescaleDB | Neo4j | Redis | MinIO | Kafka topic |
|---|---|---|---|---|---|---|
| Aircraft | SoR | status history | node | live hash | | `garuda.aircraft.status` |
| Crew | SoR | duty log | node + qualifications | live hash | | `garuda.crew.status` |
| Weapons | SoR | | stock links | counters | | `garuda.weapons.stock` |
| Airspace | SoR (geometry) | | linked zones | active set | raw OpenAIP | `garuda.airspace.activation` |
| Weather | latest summary | grid hypertable | base links | latest cache | raw JSON/GRIB | `garuda.weather.update` |
| Threats | SoR | tracks | links to targets | geo set | risk rasters | `garuda.threat.update` |
| Missions/Plans | SoR | | relationships | pending queue | | `garuda.mission.request`, `garuda.plan.event` |
| GIS/terrain | SoR | | | | DEM, cost layers | |
| ADS-B | | tracks | | live geo | snapshots | `garuda.adsb.positions` |

SoR = system of record.

## Principles
- One owner per fact; other stores are projections fed from Kafka (idempotent consumers keyed by entity id + event version).
- Event envelope:
```json
{"event_id":"uuid","type":"aircraft.status.changed","entity_id":"SU-A0123","ts":"2026-10-03T10:00:00Z","version":12,"payload":{}}
```
- Postgres is authoritative; Neo4j rebuilt from Postgres by a sync job if drifted.

## Kafka
Naming `garuda.<domain>.<event>`, partitioned by entity id, retention 7 days, compacted topics for latest-state (`aircraft.status`, `threat.update`).

## Redis key patterns
`ac:{tail}`, `crew:{id}`, `base:{id}`, `base:{id}:fmc`, `stock:{base}:{weapon}`, `threat:active` (geo), `missions:pending` (zset), `airspace:active`, `wx:base:{icao}`, `adsb:live` (geo, TTL), `plan:{id}:draft`, `lock:plan` (for solver concurrency).

## Retention
| Data | Retention |
|---|---|
| Aircraft/crew history | 90 d (compress after 7 d) |
| Weather forecast | 7 d; obs 30 d |
| ADS-B | 24 h raw, 30 d 5-min aggregate |
| Plans/audit | indefinite |
| Redis | TTL where noted, otherwise rebuilt from Postgres |

## Access pattern
Repositories behind interfaces (`AircraftRepository`, etc.) using asyncpg, neo4j async driver, redis.asyncio; harness tools only see repositories, never raw connections. Read-only DB role for LLM query tools.

## Migrations and bootstrapping
Alembic for Postgres, Cypher constraint scripts for Neo4j, `make seed` runs generators.
