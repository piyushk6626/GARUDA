# Aircraft availability local stack

This is independent of `infra/adsb`: project name `garuda-aircraft`, network
`garuda-aircraft-network`, its own volumes, and ports 25432/26379/27687/27474/29092.
It never resets ADS-B data.

From the repository root:

```powershell
if (-not (Test-Path infra/aircraft/.env)) { Copy-Item infra/aircraft/.env.example infra/aircraft/.env }
docker compose --env-file infra/aircraft/.env -f infra/aircraft/compose.yaml config --quiet
docker compose --env-file infra/aircraft/.env -f infra/aircraft/compose.yaml up -d
$env:GARUDA_POSTGRES_DSN = 'postgresql://garuda:garuda-aircraft-local-only@localhost:25432/garuda_aircraft'
uv run alembic upgrade head
uv run garuda-aircraft generate --run-id demo-1
docker compose --env-file infra/aircraft/.env -f infra/aircraft/compose.yaml --profile test up --build --abort-on-container-exit --exit-code-from store-tests store-tests
```

`down` preserves state. The explicit reset is restricted to this stack:

```powershell
docker compose --env-file infra/aircraft/.env -f infra/aircraft/compose.yaml down --volumes
```

Pins: Timescale `pg17.6-ts2.22.1` digest
`8cfcb83ee65178270a7b4f1eaf4af0854fa1639ed21c3202fb669707913faed9`;
Redis `7.4.8-alpine3.21` digest
`7aec734b2bb298a1d769fd8729f13b8514a41bf90fcdd1f38ec52267fbaa8ee6`;
Redpanda `v26.2.3` digest
`0996e23dfeb3e2a19508fb22ace04ba7e9415d5ab5b58d8998f2e38c54f0dc14`;
Python `3.13.8-slim-bookworm` digest
`61169c2bdb8e6bb44a8dfad33f569d324d52f079fded9a204b322a6fb9c9f799`;
Neo4j Community `5.26.0` manifest digest
`d5e6396795ab2b813d5c6ac820ba36f129c412ea4ad982ffccab7b8f69e9e6a5`.
The Neo4j digest was resolved with `docker manifest inspect` on 2026-10-04.

Verification performed on 2026-10-04: Compose configuration validation passed;
all four persistent stores reached healthy status; Redpanda topic and Neo4j
constraint bootstraps completed; and the shared Alembic chain reached
`20261004_aircraft` on the isolated database. `tests/test_aircraft_application.py`
passed (3 tests), and the real-container `store-tests` suite exited 0 after an
idempotency JSONB decoding fix. Neo4j Community has no database-enforced reader
role; PostgreSQL is the authoritative read fallback.
