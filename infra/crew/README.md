# GARUDA crew-status stack

This is an isolated Compose project (`garuda-crew`) for fictional crew data only:
TimescaleDB/PostGIS is authoritative, Redis and Neo4j are rebuildable projections,
and Redpanda transports the durable outbox streams. Images are pinned in the four
service Dockerfiles; the application/test images use the committed `uv.lock`.
It was validated with Docker Compose v2.39.1.

## Run

```powershell
if (-not (Test-Path infra/crew/.env)) { Copy-Item infra/crew/.env.example infra/crew/.env }
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml config --quiet
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml --profile app up -d --build
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml run --rm crew-cli generate --config config/scenarios/crew_demo.yaml --seed 42 --run-id demo-1
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml run --rm crew-cli simulate --run-id demo-1 --horizon-hours 72 --speed 60
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml run --rm crew-cli check --run-id demo-1
```

The initial bootstrap reruns safely: Alembic preserves one chain, the reader-login
job grants a SELECT-only login, Cypher constraints are idempotent, and topic setup
checks/sets the three crew topic policies. Host ports are bound only to localhost.
The test override needs Compose v2.24+ because it uses `!reset`:

```powershell
docker compose -p garuda-crew-test --env-file infra/crew/.env -f infra/crew/compose.yaml -f infra/crew/compose.test.yaml --profile test run --build --rm store-tests
docker compose -p garuda-crew-test --env-file infra/crew/.env -f infra/crew/compose.yaml -f infra/crew/compose.test.yaml down
```

`down` retains the project volumes, so a normal restart exercises retained data.
For a fresh, crew-only reset use `docker compose -p garuda-crew down -v`; do not
run that command against the aircraft, ADS-B, or airspace projects.

## Verification status

`compose config --quiet`, Alembic single-head validation, Python compilation, and
the non-container crew suite were run successfully on 2026-10-04. Container
build/start and the real-store suite are recorded in the data-layer status document
after the local Docker run; do not treat this README as proof of a run on another
machine.
