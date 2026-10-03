# ADS-B storage development stack

Four Dockerized stores support the [ADS-B implementation plan](../../docs/01_data_layer/air_traffic_adsb.md).
This supplies the storage stack plus the runnable ADS-B application layer:
offline replay, archive manifests, idempotent Kafka consumers, the Timescale
migration, Redis live projection, and typed query interfaces.

## Start and test

Use Docker Desktop's Linux containers. The pinned baseline is Linux amd64.
Run from the repository root:

```powershell
# Optional: create local overrides once; do not overwrite an existing .env.
Copy-Item infra/adsb/.env.example infra/adsb/.env
docker compose -f infra/adsb/compose.yaml config --quiet
docker compose -f infra/adsb/compose.yaml up --build -d
docker compose -f infra/adsb/compose.yaml --profile test run --build --rm store-tests
```

The test command waits for every store's health check and successful topic
initialization, then applies the ADS-B migration before executing. Use `ps --all` below to inspect readiness when
starting without tests. The first pull includes the Timescale image with bundled PostGIS and takes longer
than later cached starts. The test runner installs hash-locked dependencies inside
Docker; no host Python environment or OpenSky credentials are needed.

One file, `tests/integration/test_adsb_stores.py`, contains four cases: Timescale
spatial history and continuous aggregation, Redis GEO with per-aircraft expiry,
Redpanda keyed event delivery/replay, and MinIO snapshot/checksum round-trip.
`tests/integration/test_adsb_application_e2e.py` adds a labelled fabricated
replay through Redpanda, the independent history/live consumers, TimescaleDB,
and Redis. All five cases passed against the Docker stack on 2026-10-04. Each case removes
only its own uniquely named test resources. Select one with,
for example, `store-tests -k minio` appended to the test command.

## Local endpoints

| Service | Host endpoint | Container endpoint |
|---|---|---|
| PostgreSQL / Timescale / PostGIS | `localhost:15432` | `timescaledb:5432` |
| Redis | `localhost:16379` | `redis:6379` |
| Kafka API | `localhost:19092` | `redpanda:9092` |
| MinIO S3 API | `http://localhost:19000` | `http://minio:9000` |
| MinIO console | `http://localhost:19001` | `http://minio:9001` |

Compose defaults use the local-only credentials shown in `.env.example`; override
them in the ignored `.env` as needed. The test runner automatically uses the same
credentials. Published ports bind to localhost. Redpanda is a single development
broker without authentication; clients outside this machine are out of scope.

## Image pins

The corresponding Dockerfiles record immutable SHA-256 image digests:

| Component | Version / source |
|---|---|
| Timescale + PostGIS | `timescale/timescaledb-ha:pg17.6-ts2.22.1` |
| Redis | `redis:7.4.8-alpine3.21` |
| Redpanda | `docker.redpanda.com/redpandadata/redpanda:v26.2.3` |
| MinIO | `coollabsio/minio:RELEASE.2025-10-15T17-29-55Z` |
| Python test runner | `python:3.13.8-slim-bookworm` |

MinIO uses a prebuilt third-party community distribution from
[Coollabs](https://github.com/coollabsio/minio), compiled from the named upstream
release. Its digest pins the entire distributed image, including the included
`mc` client used for readiness. No local source compilation or AIStor license
setup is needed. This is a prototype dependency: upstream community MinIO is no
longer maintained, so assess support before a production deployment.

To intentionally refresh test dependencies, use `uv` on the host:

```powershell
uv pip compile infra/adsb/tests/requirements.in --python-version 3.13 --python-platform x86_64-manylinux_2_28 --generate-hashes --output-file infra/adsb/tests/requirements.lock
```

Review changed pins and rerun the tests after an upgrade. Save built images with
`docker image save` if the demo must work without registries.

## Initialization and data lifecycle

TimescaleDB and PostGIS extensions initialize on the first PostgreSQL volume
startup. Domain tables and retention policies use Alembic. The topic init
service creates `garuda.adsb.positions` and `garuda.adsb.dead_letter`, each with
three partitions, replication factor one, delete cleanup, and seven-day retention.
It can safely rerun against existing topics. The archive adapter initializes the
`garuda-adsb-raw` bucket on first use and sets a 30-day lifecycle rule for its
checksum-addressed raw snapshots; pending manifests are reconciled after an
interrupted publication.

## Application commands

The root `pyproject.toml` and `uv.lock` lock application dependencies.

```powershell
uv sync --extra dev
uv run alembic upgrade head
uv run python -m backend.app replay --replay-id demo-1 --start 2026-10-04T00:00:00Z --seed 42 --background 10
docker compose -f infra/adsb/compose.yaml --profile app up --build adsb-history-worker adsb-live-worker
```

Replay is clearly labelled fabricated data and uses a `replay:<id>` source ID;
it is not a live-airspace claim. `poll --once` archives a successful response
before Kafka publication. It supports anonymous mode or optional
`ADSB_OPENSKY_CLIENT_ID` / `ADSB_OPENSKY_CLIENT_SECRET` OAuth credentials.
The migration service runs before the workers and is written to upgrade existing
volumes or initialize fresh ones. The existing-volume upgrade was verified; a
clean-volume reset was intentionally not run because this workspace's persistent
data was preserved.

Stop the stack while retaining all named volumes:

```powershell
docker compose -f infra/adsb/compose.yaml down
```

For an intentional clean reset of this stack's stored data only:

```powershell
docker compose -f infra/adsb/compose.yaml down --volumes
```

Inspect health and startup logs with:

```powershell
docker compose -f infra/adsb/compose.yaml ps --all
docker compose -f infra/adsb/compose.yaml logs --tail 50
```
