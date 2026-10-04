# Crew Status: Implementation Plan

**Status: planned; implementation and Docker verification are pending.**
Prepared on 2026-10-04 for `dev-1`. The original domain specification below
is retained as context; the implementation decisions following it resolve its
abbreviated examples and ambiguities.

## Purpose
Who can fly what, when, and how fatigued they are.

## Source
Synthetic (no real personnel data; use Faker with Indian-name locale `en_IN`, or pseudonymous callsigns).

## Schema
| Field | Type | Unit | Constraints |
|---|---|---|---|
| crew_id | text | | PK |
| callsign | text | | |
| role | text | | PILOT / WSO / LOADMASTER / AEW_OP |
| sqn_id, base_id | text | | FK |
| qualifications | jsonb | | `{type: [roles]}`, e.g. `{"Su-30MKI":["BVR","AG"]}` |
| currency | jsonb | | last-flown dates per qualification, night, AAR |
| flight_hours_total / last_30d | real | h | |
| duty_hours_24h | real | h | max 14 |
| rest_hours_since_last_duty | real | h | min 12 |
| fatigue_score | real | 0-1 | from fatigue model |
| medical_status | text | | FIT / LIMITED / GROUNDED |
| leave_until | timestamptz | | nullable |
| current_assignment | text | | mission_id |
| updated_at | timestamptz | | |

## Generation rules
- ~2 crew per fighter airframe (pilot, plus WSO for two-seaters); ~300 total.
- Experience: total hours ~ Gamma(3, 900); flight leads = top 25%.
- Qualifications: each crew 1 primary type, 20% a secondary; AAR currency 60%; night 70%.
- Medical: 93% FIT, 5% LIMITED, 2% GROUNDED. Leave: 5% on leave.
- Duty/rest evolves via simulator; fatigue updated hourly.

## Storage
```sql
CREATE TABLE crew (
  crew_id text PRIMARY KEY, callsign text, role text, sqn_id text, base_id text,
  qualifications jsonb, currency jsonb, flight_hours_30d real,
  duty_hours_24h real, rest_hours real, fatigue_score real,
  medical_status text, leave_until timestamptz, current_assignment text
);
CREATE INDEX ON crew USING gin (qualifications);
```
TimescaleDB `crew_duty_log(time, crew_id, duty_hours_24h, fatigue_score)`. Neo4j `(:Crew)-[:QUALIFIED_ON]->(:Aircraft type)` and `[:MEMBER_OF]`. Redis `crew:{id}`. Kafka `garuda.crew.status`.

## Privacy
Pseudonymise; never ingest real personal data. Role-based access in the API.

## Consumers
Crew scheduling CP-SAT, fatigue model, MILP assignment (crew availability).

## 1. Scope and branch alignment

Implement reproducible synthetic crew generation, authoritative current state,
duty/flight/rest intervals, hourly fatigue snapshots, availability queries,
durable event delivery, and rebuildable Redis/Neo4j projections. Deliver a
Docker stack and **one executable Python testing file containing a named case
for each database/store**, plus generation, simulation, and recovery cases.

The user's request defines these deliverables. [Storage architecture](storage_architecture.md)
and [synthetic generation](synthetic_data_generation.md) provide design context;
their illustrative SQL, CLI commands, and pipeline descriptions are not assumed
to be implemented or to authorize additional implementation in this planning task.
The scheduler and [fatigue model](../03_optimization_layer/crew_fatigue_model.md)
documents supply downstream data requirements. Full CP-SAT/MILP optimization,
ML fatigue calibration, and a new HTTP authentication framework are later work.

Branch comparison performed without checking out either branch:

| Reference inspected | Finding and decision for `dev-1` |
|---|---|
| Local `main` at `9d4eb48` | Uses `backend/src/garuda`, `backend/alembic`, root `docker-compose.yml`, `config/scenario.yaml`, and `backend/tests/test_seed_integration.py`. Provides async database drivers, synthetic scenario generation, bootstrap roles/constraints/topics, and seed examples for missions/threats/weapons. No crew implementation exists there. |
| `main:backend/src/garuda/storage/seed.py` | Writes SQL, graph, cache, then Kafka sequentially. Preserve its generated-scenario concept; use transactional state + outbox on `dev-1` so partial external-store failure can recover. |
| `main:backend/src/garuda/storage/neo4j_sync.py` | Clears several domain labels, including Base, before rebuilding. Crew reconciliation must be scoped to the selected run and crew-owned nodes/edges so shared references survive. |
| Committed `dev-1` at `b5caee8` | Uses `backend/app`, root `alembic.ini`, `backend/migrations`, root `pyproject.toml`/`uv.lock`, and `infra/adsb`/`infra/aircraft`. Follow these paths and async command-service/repository/outbox patterns. |
| Current working tree | Contains existing uncommitted airspace implementation, including migration `20261004_airspace` after `20261004_aircraft`, isolated Compose/test overrides, and reader bootstrap. Plan against this working tree while preserving those changes. |

Use local `main` as a reference, not as code to merge wholesale. The old
`python -m garuda.generate` example does not describe the active branch's package.
Add `garuda-crew` alongside existing entrypoints. Aircraft currently has placeholder
worker/simulate/reconcile/query CLI handlers; crew commands must execute their
real application paths. Its broker test checks producer flush without consuming;
crew verification must prove delivery with a real consumer.

## 2. Explicit decisions resolving the specification

- PostgreSQL owns crew identity, qualifications, currency, squadron/base membership,
  assignment state, and duty/flight intervals. TimescaleDB holds projected hourly
  and change-event history. Redis and Neo4j are disposable projections.
- One TimescaleDB/PostgreSQL service holds both ordinary relational tables and
  the history hypertable. Enable PostGIS because earlier migrations require it.
- Canonical names are `flight_hours_total`, `flight_hours_last_30d`, and
  `rest_hours_since_last_duty`. Accept `flight_hours_30d`, `last_30d`, and
  `rest_hours` only as documented input aliases; emit/store canonical names.
  Include `updated_at`, which the abbreviated SQL omits.
- `role` is the crew position enum. Values inside `qualifications`, such as BVR
  and AG, are mission qualifications, not crew-position roles. Store canonical
  aircraft type names using the existing aircraft alias parser; declare permitted
  position/type/qualification combinations in a fictional crew catalog.
- Model qualification edges to `AircraftType`, rather than an individual tail.
  A type qualification must not imply clearance on every mission or current currency.
- Treat 14 h maximum duty and 12 h minimum rest as eligibility/dispatch policy,
  not blanket storage constraints. Store real synthetic ineligible states, including
  rest below 12 h and overload injections. SQL bounds duty to 0..24 h and rest to
  nonnegative finite values; routine dispatch rejects projected duty above 14 h.
- `LIMITED` is ineligible by default until explicit restriction rules exist;
  `GROUNDED` is always ineligible. RED fatigue is strictly `> 0.7`, following
  the fatigue document; 0.7 is AMBER. Configurable defaults remain demo policy.
- Use pseudonymous callsigns only in v1. Faker `en_IN` names are optional future
  synthetic presentation data, so no new Faker dependency is needed now.
- `current_assignment` is an external mission identifier, nullable. There is no
  mission table on `dev-1`; do not add a foreign key to a nonexistent table.
  Dispatch needs a validated synthetic sortie/mission reference supplied through
  the command contract; future mission integration will replace that adapter.

## 3. Dockerized stores and reproducibility

Create `infra/crew/compose.yaml`, default project `garuda-crew`, with project-scoped
networks and named volumes. Avoid fixed container/network/volume names so an
independent `garuda-crew-test` project can run concurrently. Bind development
ports to localhost and allow `CREW_*` overrides.

| Service | Responsibility | Required Dockerfile | Proposed host port |
|---|---|---|---|
| PostgreSQL / TimescaleDB / PostGIS | Crew state, intervals, reference FKs, outbox, duty history | `infra/crew/timescaledb/Dockerfile` | `45432` |
| Redis | `crew:{id}` live hashes | `infra/crew/redis/Dockerfile` | `46379` |
| Neo4j Community | Crew/type/squadron/base relationships | `infra/crew/neo4j/Dockerfile` | Bolt `47687`, HTTP `47474` |
| Redpanda | Kafka-compatible status, history-event, dead-letter topics | `infra/crew/redpanda/Dockerfile` | `49092` |

Redpanda is a broker, but is included in the reproducibility and test requirements.
MinIO has no crew ownership responsibility and is not required for this domain.
Reuse the full tag-and-digest pins in the existing aircraft Dockerfiles for
TimescaleDB, Redis, Neo4j, Redpanda, and Python. Verify pulls/builds during
implementation; do not describe them as newly verified in this plan. Retain
`linux/amd64` support consistent with the current stacks. Record image pins and
the tested Docker/Compose versions in `infra/crew/README.md`.

Provide separate pinned app and test Dockerfiles, frozen application dependencies,
and hash-verified test requirements. Reuse existing NumPy, Pydantic, SimPy,
asyncpg, aiokafka, Redis, and Neo4j packages. Test-only confluent-kafka is already
used by store suites; add its compatible exact pin to the crew test lock.
Do not introduce floating image tags, unlocked installs, or unpinned apt packages.

Commit `.env.example`; ignore the actual `.env`. Use `CREW_*` runtime settings
and `GARUDA_POSTGRES_DSN` for migration jobs. App containers use service DNS;
host commands use published ports. Kafka internal and external advertisements
must match those addresses, with the external port derived from the same setting
as the published port. Extend `.dockerignore` narrowly for crew build inputs
without exposing environment files; preserve existing changes.

Readiness: `pg_isready`, authenticated Redis `PING`, authenticated Neo4j
`RETURN 1`, and Redpanda cluster health. Add persistent SQL/Redis AOF/Neo4j/broker
volumes and resource settings. Bootstrap jobs apply Alembic, Cypher constraints,
topic configuration, and a real read-only SQL login. Jobs must rerun safely and
verify existing configuration. Workers/tests depend on healthy stores and
successful bootstrap. Extension/role first-start scripts alone are insufficient
for upgrading existing volumes; migrations grant the reader group independently.
Keep shell scripts LF as configured by `.gitattributes`.

`compose.test.yaml` removes host port publication and supplies test-only settings;
record the minimum Compose version if using the airspace `!reset` pattern.
Normal `down` preserves volumes. Document a separate explicit reset scoped to
the crew project and never touch aircraft/ADS-B/airspace volumes.

## 4. Contracts, reference ownership, and SQL migration

Add frozen, extra-forbidden Pydantic models `CrewState`, `CrewCommand`,
`CrewEvent`, `CrewQuery`, `CrewAvailabilityQuery`, `DutyInterval`, and scenario/
catalog configuration. Reject nonfinite numeric values, naive timestamps,
unknown roles/types, invalid JSON structures, future last-flown dates relative
to the snapshot, negative hours, and last-30-day hours exceeding total hours.
`currency` contains last-flown UTC dates by aircraft type and qualification,
plus explicit nullable night/AAR dates. Missing/null dates mean not current.
Qualifications alone never supply currency.

Persist version, event identity, logical time, recording time, run ID, primary
type, and `is_flight_lead` as well as the full original schema. Version starts
at one; event IDs use a crew-specific UUID5 namespace with `(run_id, crew_id,
version)`. Embed run ID in crew IDs so `crew:{id}` keys are globally distinct.
Keep callsigns unique per run and never derive IDs from generated names.

Add `backend/migrations/versions/20261004_04_crew.py`, revision `20261004_crew`,
following the current working-tree head `20261004_airspace`. Recheck the head
before implementation if concurrent work changes it; maintain one linear chain.
Apply the whole chain in the crew database, including earlier Timescale/PostGIS
requirements. Do not create another Alembic directory or head.

| Planned table in schema `crew` | Purpose and key constraints |
|---|---|
| `scenario_run` | Run ID, seed, fixed UTC start, config/catalog/reference hashes, generator/model versions, logical clock, RNG/checkpoint state, lifecycle status; references the corresponding aircraft reference run |
| `crew` | `crew_id` PK; full validated state, version and unique last-event ID; `(run_id, base_id)` and `(run_id, sqn_id)` FKs to aircraft reference tables; primary type FK; finite-hour and enum checks |
| `crew_qualification` | Normalized `(crew_id, aircraft_type, qualification)` rows derived transactionally from state JSON; aircraft-type FK and permitted-role checks; supports efficient eligibility joins |
| `duty_interval` | UUID key, crew/run, UTC start/end, disjoint duty/rest/sleep segments, flight tags and sortie/mission reference; interval checks and per-crew no-overlap validation; open interval at most one |
| `crew_event_identity` | Immutable full envelope and canonical payload hash; unique event ID and `(run_id, crew_id, version)` for replay/conflict detection |
| `crew_outbox` | Event reference, idempotency key and command hash, per-topic publication markers, attempts/backoff, history projection marker; unique `(run_id, crew_id, idempotency_key)` |
| `crew_duty_log` | Hypertable: logical `time`, run/crew/version/event ID, duty/rest/flight totals, fatigue/band, full state snapshot and model provenance; primary key includes time |

Reuse existing `aircraft.base`, `aircraft.squadron`, and `aircraft.aircraft_type`
references rather than inventing unrelated crew base/squadron tables. A crew
bootstrap adapter calls the pure aircraft generator for the configured reference
scenario and loads just the run/catalog/base/squadron references in a fresh crew
database. It need not seed aircraft state or publish aircraft events.
Record the reference scenario hash separately from the crew scenario hash.
On a shared database, validate existing reference values/hashes and fail on
conflicts instead of overwriting them. Home squadron/base assignment is independent
of the crew member's current base. Do not move a squadron when a crew member transfers.

Use a GIN index on qualifications, state lookup indexes on run/base/role/medical,
an interval index on crew/start/end, history lookup on run/crew/time/version,
and a partial pending-outbox index. The normalized qualification rows and JSON
snapshot are maintained in the same transaction; projection consumers never
write qualifications back into SQL.

Retain history 90 days and compress after seven days using APIs compatible with
the pinned Timescale release. Keep sufficient interval/checkpoint history for
rolling 30-day and future seven-day calculations. Use logical-time retention for
fixed-clock demo runs; wall-clock retention must not immediately erase archived
synthetic runs. Explicitly choose either live database policies or logical-clock
cleanup per run mode and test the cutoff boundaries. Event-identity/outbox cleanup
must preserve replay/idempotency guarantees and pending deliveries.

## 5. Deterministic generation

Create `config/crew_types.yaml` with permitted positions, primary/secondary
qualifications, staffing counts, night/AAR capability, and fictional currency
windows. Create `config/scenarios/crew_demo.yaml` referencing the existing
aircraft demo, fixed T0, seed 42, 72-hour horizon, and staffing/policy overrides.
Seed precedence is CLI `--seed`, then `GARUDA_SEED`, then scenario seed.
Keep generation pure: no databases, network, or current wall clock.

The default demo contains 300 people for the existing 150-aircraft fleet.
Use two personnel per airframe as a configurable roster-sizing convention:
Su-30MKI has one PILOT and one WSO; C-130J one PILOT and one LOADMASTER;
AEW&C one PILOT and one AEW_OP; other types two PILOT roster members.
This produces 248 PILOT, 40 WSO, 8 LOADMASTER, and 4 AEW_OP records. It is a
fictional personnel pool, not a claim about real seat requirements. The catalog
defines sortie seat requirements separately. Explicit per-type staffing overrides
take precedence over the approximate total; reject a configured total that
contradicts its staffing rules instead of silently truncating required roles.

Use independent documented NumPy SeedSequence streams for identities/placement,
experience, qualification/currency, medical/leave, initial intervals, and simulation.
Iterate types/squadrons/crew in a stable sorted order. Stable IDs and callsigns
must survive a changed random seed; generated attributes and snapshot hash change.
Record generator/catalog/model versions and hashes in the run manifest.

- Draw total hours from Gamma(shape=3, scale=900). Select flight leads as the
  top 25% of PILOT experience within primary-type pools, with deterministic
  rounding and crew-ID tie breaks. Never classify support positions as flight leads.
- Give every crew member one primary type. Assign a compatible secondary to
  20% of the whole roster, sampled from roles with a legal secondary type;
  fail an impossible configuration rather than assigning invalid qualifications.
- Target 60% AAR currency among AAR-capable, qualified crew and 70% night currency
  among night-capable crew. Document denominators and integer rounding. Generate
  both recent and expired/null dates so eligibility exercises currency failures.
  Proposed demo windows: qualification 90 d, night 90 d, AAR 180 d; these are
  versioned configurable assumptions, not inferred real regulations.
- Use deterministic largest-remainder allocation plus seeded shuffling for
  93/5/2 medical percentages and 5% leave. Future `leave_until` values block
  availability; expired dates remain representable. Default assignments are null.
- Generate a 30-day interval history ending at T0, including rest/sleep opportunity
  and flight tags. Calculate current duty, rest, and recent flight hours from
  those intervals so the state is consistent, rather than drawing unrelated totals.
  Ensure recent flights do not exceed sampled lifetime experience.

Commit a canonical seed-42 JSON snapshot, SHA-256 file, and manifest. Exclude
wall-clock recording timestamps from the reproducibility hash. Validate category
counts, references, role/type combinations, and deterministic experience-distribution
checks over a larger fixed synthetic sample; do not require a noisy KS result
from only 300 people or add SciPy solely for this check.

## 6. Commands, simulation, and availability policy

`CrewCommandService` is the only write path. Lock the selected crew row, check
expected version and nondecreasing logical time, apply validated changes, update
intervals/qualification rows, recompute state, and insert immutable event/outbox
rows in one SQL transaction. An identical idempotency key + command hash returns
the original event; different content with the same key is a conflict. SQL JSONB
decoding and canonical hashing must behave identically on every path.

Seed references, all crew states, initial intervals and outbox records in one
transaction under a run-scoped lock. Mark complete only after the transaction
succeeds. Same run/config is a no-op; changed config requires a new run ID.
Support seed retry after process failure without a partially completed roster.

Support duty start/end, sortie start/end, rest/sleep windows, transfer, qualification/
currency edits, medical/leave changes, and explicit scenario injections. Flight
is a tagged part of duty, not a second overlapping duty interval. Sortie completion
adds actual flown duration to lifetime hours and refreshes only the qualifications
and night/AAR features exercised. Reject negative or overlapping durations,
duplicate assignments, backwards time, and incompatible role/type combinations.
Represent sleep as a restful segment distinct from adjacent nonsleep rest;
both contribute to continuous rest, but only sleep drives reservoir recovery.
Split duty at flight boundaries when needed so flight durations remain measurable
without creating overlapping segments.

Compute rolling duty by interval intersections with `[t-24h, t)`; current open
duty is clipped at t. Compute flight totals with `[t-30d, t)` and expose seven-day
totals for future scheduling. Rest is continuous time since the most recent duty
ended and is zero while on duty; time off duty is not automatically sleep.
Persist checkpoints and RNG state so restart reproduces uninterrupted results.

Add a minimal pure fatigue module supporting the documented reservoir/circadian/
time-on-task model and explicit synthetic sleep opportunities. Version/persist
model parameters and latent state; clip score to 0..1 and update hourly and at
duty/sortie transitions. Use configured local circadian time (demo Asia/Kolkata)
while storing UTC. Demonstrate sensible wake/sleep trajectories using canonical
scenarios. ML calibration and full projected-plan scoring remain later work.

Run a logical-clock SimPy simulator with configurable wall-clock speed. Hourly
ticks update fatigue, rolling hours, rest, leave/currency expiry, and eligibility.
Dispatch checks medical, leave, assignment, qualification/currency, required
position, current rest, projected duty including buffers, and fatigue. Multiple
sorties within one duty block do not each require a new 12-hour rest period;
the duty-block boundary determines that rule. Emergency overload is only an
explicit synthetic injection, persisted with a policy-violation reason.

Provide `CrewRepository` methods `get_state`, `list_crew`, `get_available_crew`,
`get_duty_history`, and `get_qualification_summary`. Parameterize and bound SQL;
use stable pagination and typed outputs with run ID, evaluation time, data time,
version, fatigue band/model version, and blocking reasons. Eligibility is derived
at the requested time, not persisted as an evergreen `available` flag. Evaluate
slot end time for projected duty and leave overlap. Historical reads use stored
snapshots/intervals rather than today's qualifications joined onto past history.
Return unavailable historical coverage explicitly and reject arbitrary future
eligibility requests without a supplied schedule projection.

Harness-facing reads use a `garuda_crew_reader` group and an actual LOGIN member
with SELECT-only grants and timeout. Writer credentials stay in command/worker
adapters. Add a scoped access-policy boundary for repository exposure and test
unauthorized requests; full HTTP auth is deferred until an API exists. Neo4j
Community does not supply a database-enforced reader role, so harness access
uses the SQL reader path instead of handing out graph credentials.

## 7. Events, projections, and recovery

Emit the standard `{event_id, type, entity_id, ts, version, payload}` envelope;
use `crew.status.changed` with payload schema version, run ID, full state snapshot,
command kind, interval changes, model provenance, and policy reasons. Publish
with key `crew_id` to three-partition topics:

| Topic | Policy and consumer |
|---|---|
| `garuda.crew.status` | Compacted latest state; Redis and Neo4j groups |
| `garuda.crew.events` | Delete retention seven days; history consumer receives every change/tick |
| `garuda.crew.dead_letter` | Delete retention seven days; invalid/conflicting event diagnostics |

Topic creation must verify partition count, retention and cleanup policy on
existing topics. Compacted status cannot preserve every hourly history sample,
so history uses the uncompacted stream and durable SQL envelopes for catch-up.
Persist SQL events longer than broker retention where needed to recover missing
history within the 90-day window.

Implement a real outbox relay with acknowledged publication, durable per-topic
markers, bounded retry/backoff, and per-crew ordering. Serialize relay ownership
for v1 so two relays cannot interleave crew versions. A crash after broker ACK
but before SQL marking may duplicate an event; this is expected at-least-once
delivery, handled by consumers. Verify schema/event/entity/version consistency
and payload identity before applying a projection.

Consumers use independent groups, disable auto-commit, and commit a partition's
processed offset only after a successful write or acknowledged dead-letter
publication. Malformed payloads and conflicting identity hashes are dead-lettered;
transient SQL/Redis/Neo4j failures retry without advancing the offset.

- History inserts identity + hypertable snapshot transactionally. Its global
  identity table rejects conflicting `(run, crew, version)` content even though
  hypertable uniqueness includes time. Exact duplicates are harmless. Retained
  older versions can fill history without regressing current state.
- Redis atomically compares version and writes the complete `crew:{id}` hash
  using Lua. Include currency/qualification JSON, assignment, duty/rest, fatigue,
  logical time and event ID. Never use a cached eligibility flag for dispatch.
  Validate run/version/freshness before serving reads; fall back to SQL when
  stale, missing, or mismatched. Hourly events alone cannot govern a mid-hour
  leave/currency/rest boundary; repository checks use their requested time.
- Neo4j creates unique Crew and AircraftType identities and run-scoped squadron/
  base references. Project `QUALIFIED_ON` with qualification properties,
  `MEMBER_OF`, and `LOCATED_AT`. Version-guard updates in a serialized consumer
  transaction; explicitly remove revoked qualifications and old membership/location
  edges while preserving shared type/reference nodes. Test a transfer separately
  from home-base membership. Do not reuse the aircraft projection's assumption
  that current base is squadron home base.

Reconciliation takes an SQL snapshot with a version watermark and repairs missing,
stale, or corrupted projections, including drift at the same version. A force
repair must never overwrite a concurrently applied newer version: compare-and-set
Redis writes and serialize/lock graph repairs with consumer writes. Scope cleanup
to run-owned crew data, preserve other domains, and replay updates after the
snapshot watermark. Outbox/history replay and consumer offsets make restart
recovery explicit; reseeding the roster is not a recovery strategy.

## 8. Planned files and executable commands

Paths below are planned deliverables, not files created by this planning task.

```text
backend/app/crew_config.py
backend/app/crew_cli.py
backend/app/schemas/crew.py
backend/app/data/bootstrap/crew.py
backend/app/data/generators/crew.py
backend/app/data/services/crew.py
backend/app/data/repositories/crew.py
backend/app/data/outbox/crew.py
backend/app/data/consumers/crew_history.py
backend/app/data/consumers/crew_live.py
backend/app/data/consumers/crew_graph.py
backend/app/data/reconciliation/crew.py
backend/app/data/simulation/crew.py
backend/app/data/fatigue/crew.py
backend/migrations/versions/20261004_04_crew.py
config/crew_types.yaml
config/scenarios/crew_demo.yaml
infra/crew/compose.yaml
infra/crew/compose.test.yaml
infra/crew/.env.example
infra/crew/README.md
infra/crew/timescaledb/Dockerfile
infra/crew/timescaledb/init/020_extensions.sql
infra/crew/timescaledb/init/030_reader.sh
infra/crew/redis/Dockerfile
infra/crew/redis/redis.conf
infra/crew/neo4j/Dockerfile
infra/crew/neo4j/init/constraints.cypher
infra/crew/redpanda/Dockerfile
infra/crew/redpanda/init-topics.sh
infra/crew/app/Dockerfile
infra/crew/tests/Dockerfile
infra/crew/tests/requirements.in
infra/crew/tests/requirements.lock
tests/integration/test_crew_stores.py
tests/fixtures/crew/seed42_snapshot.json
tests/fixtures/crew/seed42_snapshot.json.sha256
tests/fixtures/crew/seed42_manifest.json
```

Update root `pyproject.toml`/`uv.lock` as needed, relevant package exports,
`.dockerignore`, and README. Create new package initializers where needed.
Use shared helpers only where existing behavior is preserved and checked.

Implement `garuda-crew generate`, `simulate`, `worker <outbox|history|live|graph>`,
`reconcile`, `query`, and `check`. Generation/simulation accept config, seed,
run ID, horizon, and speed as appropriate. Every command must perform work or
fail clearly; placeholder success output is not completion.

Expected commands from the repository root **after implementation**:

```powershell
if (-not (Test-Path infra/crew/.env)) {
    Copy-Item infra/crew/.env.example infra/crew/.env
}
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml config --quiet
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml --profile app up -d --build
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml run --rm crew-cli generate --config config/scenarios/crew_demo.yaml --seed 42 --run-id demo-1
docker compose --env-file infra/crew/.env -f infra/crew/compose.yaml run --rm crew-cli simulate --run-id demo-1 --horizon-hours 72 --speed 60
uv run pytest -q tests/integration/test_crew_stores.py -m "not integration"
docker compose -p garuda-crew-test --env-file infra/crew/.env -f infra/crew/compose.yaml -f infra/crew/compose.test.yaml --profile test run --build --rm store-tests
docker compose -p garuda-crew-test --env-file infra/crew/.env -f infra/crew/compose.yaml -f infra/crew/compose.test.yaml down
```

Define `crew-cli` with the real entrypoint and bootstrap dependencies; app-profile
workers start only after initialization. The test runner connects only to its test
project, sets the integration flag, and exits nonzero on failure. Document fresh
test-volume startup, ordinary persistent restart, migration of pre-crew volumes,
and explicit crew-only reset separately.

## 9. One consolidated test file

Create **only one new executable Python test module**:
`tests/integration/test_crew_stores.py`. It contains the following named cases
with local fixtures/helpers. Snapshot/manifest files and requirement locks are
supporting artifacts, not additional test modules. PostgreSQL and TimescaleDB
share one container but receive separate cases for their distinct responsibilities.

Mark only container cases `integration`. Pure cases run without Docker.
`GARUDA_CREW_INTEGRATION=1` enables real-container cases and is always set in
the test service. Disabled integration cases may skip; enabled cases fail on
unavailable services rather than silently skipping. Delay integration-only imports
so the pure suite does not require test-container-only Kafka packages.

| Case in `test_crew_stores.py` | Required evidence |
|---|---|
| `test_crew_validation_and_generation` (parameterized) | Complete fields/aliases, enum/JSON/UTC/finite bounds, 300-person role counts, legal secondary types, category denominators, reference integrity, identical seed-42 snapshot/hash, stable IDs with changed attributes for another seed |
| `test_crew_duty_fatigue_and_eligibility` (parameterized) | Exact interval intersections at 24 h/30 d boundaries, no flight/duty double-counting, rest reset, sleep vs rest, canonical wake/sleep fatigue, band boundary 0.7, 14 h/12 h dispatch boundaries, leave/currency expiry mid-hour, role/type/night/AAR blocking reasons |
| `test_postgres_crew_state_and_outbox` | Full migration chain; FKs/GIN/reader login; complete transactional seed; SQL rollback; qualification JSON/rows agree; duplicate key replay and conflicting key rejection; expected-version concurrency; no double assignment; reader SELECT succeeds and writes fail; scoped access denied |
| `test_timescaledb_crew_duty_log` | Real hypertable and indexes; initial/hourly/change snapshots; global event identity; duplicate replay; late valid events and conflicting duplicates; correct historical snapshot; seven-/90-day retention/compression configuration and controlled cutoff behavior |
| `test_redis_crew_live_hash` | Complete fields; atomic updates; duplicate/older versions ignored; run isolation; event identity conflicts rejected; requested-time eligibility remains correct; stale cache SQL fallback; missing and same-version corrupted hashes repaired |
| `test_neo4j_crew_qualifications` | Real constraints; AircraftType edges, positions/qualification properties, squadron/base membership; revocation and transfer remove obsolete edges; duplicate/stale updates; shared references preserved; serialized same-version drift repair |
| `test_redpanda_crew_status_roundtrip` | Bootstrapped topics/partitions/policies; ACK plus actual consumed key/full envelope; per-crew ordered versions; independent groups and offset replay; malformed/conflicting-event dead letters; transient failures do not advance offsets |
| `test_crew_pipeline_and_recovery` | Actual CLI seed/simulate/workers through SQL -> outbox -> Kafka -> history/Redis/graph -> repository; identical repeat seed; broker outage preserves commands; ACK-before-marker replay; SQL transaction rollback; consumer-write-before-commit replay; restart/checkpoint equivalence; drift repair with concurrent newer state |

Use unique test run/crew IDs, bounded readiness/consumer polling, deterministic
clocks, and cleanup of test-owned rows/keys/nodes only. Avoid `FLUSHDB` and global
graph deletes. Broker failure injections occur only on the isolated test project.
Roundtrip tests consume the bootstrapped domain topics instead of substitute
topics that bypass configuration checks. Pipeline tests launch actual commands
and consumer loops, not only direct calls to projection methods.

## 10. Implementation sequence and acceptance gates

1. **Contracts and deterministic fixtures.** Resolve catalog/policy assumptions,
   implement models/config/generator/interval arithmetic/fatigue core, and commit
   snapshot/hash/manifest. Gate: pure cases pass and the complete roster reconciles
   with aircraft references and generated interval history.
2. **Docker and migration.** Add every service Dockerfile, Compose/test override,
   pinned locks, health/bootstrap jobs, reader login and the linear SQL migration.
   Gate: configuration validation, fresh builds, healthy stores, repeat bootstrap,
   and upgrade of an existing pre-crew database all succeed.
3. **Authoritative write/read paths.** Implement reference bootstrap, atomic seed,
   versioned commands, interval-derived state, availability and bounded history.
   Gate: SQL constraints, rollback/idempotency/concurrency, permissions, and
   eligibility boundary cases pass in the consolidated module.
4. **Real delivery and projections.** Implement relay, history/live/graph consumer
   loops, dead letters, reconciliation and worker lifecycle. Gate: real Kafka
   consume/offset evidence and store-specific behavior/recovery cases pass.
5. **Simulation and operational integration.** Wire the logical-clock simulator,
   persistent checkpoints, hourly updates, CLI query/check/reconcile, and README.
   Gate: restart matches uninterrupted output and the 72-hour demo updates all
   stores with no unresolved outbox backlog or projection drift.
6. **Final reproducibility check.** Run the single crew suite in a fresh isolated
   test project, repeat it against retained crew volumes, and check existing
   affected domain commands/migrations/tests without resetting their stacks.
   Gate: failures propagate to the shell, all relevant checks pass, and the
   documented commands recreate the same logical data from committed artifacts.

Implementation is complete only when the working Dockerfiles, migration,
generation/simulation/worker commands, one consolidated test module, fixed-seed
fixtures, and recovery behavior are delivered and verified. Update this document
with actual verification results at that point; this planning change makes no
claim that crew containers or tests have been created or run.
