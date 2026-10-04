"""Crew authority, durable events, outbox, and duty history.

Revision ID: 20261004_crew
Revises: 20261004_airspace
"""
from alembic import op

revision = "20261004_crew"
down_revision = "20261004_airspace"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS crew")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.scenario_run (
      run_id text PRIMARY KEY CHECK(run_id ~ '^[A-Za-z0-9_-]+$'), seed bigint NOT NULL, start_at timestamptz NOT NULL,
      logical_clock timestamptz NOT NULL, configuration_hash text NOT NULL, catalog_hash text NOT NULL,
      reference_hash text NOT NULL, aircraft_run_id text NOT NULL REFERENCES aircraft.scenario_run(run_id),
      generator_version text NOT NULL, model_version text NOT NULL, rng_state jsonb NOT NULL DEFAULT '{}',
      lifecycle_status text NOT NULL DEFAULT 'seeded' CHECK(lifecycle_status IN('seeded','running','complete','failed')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,configuration_hash))""")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.crew (
      crew_id text PRIMARY KEY, run_id text NOT NULL REFERENCES crew.scenario_run(run_id) ON DELETE CASCADE,
      callsign text NOT NULL, role text NOT NULL CHECK(role IN('PILOT','WSO','LOADMASTER','AEW_OP')),
      sqn_id text NOT NULL, base_id text NOT NULL, primary_type text NOT NULL REFERENCES aircraft.aircraft_type(type),
      qualifications jsonb NOT NULL, currency jsonb NOT NULL, flight_hours_total double precision NOT NULL,
      flight_hours_last_30d double precision NOT NULL, duty_hours_24h double precision NOT NULL,
      rest_hours_since_last_duty double precision NOT NULL, fatigue_score double precision NOT NULL,
      medical_status text NOT NULL CHECK(medical_status IN('FIT','LIMITED','GROUNDED')), leave_until timestamptz NULL,
      current_assignment text NULL, is_flight_lead boolean NOT NULL DEFAULT false, logical_time timestamptz NOT NULL,
      updated_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), version bigint NOT NULL,
      last_event_id uuid NOT NULL UNIQUE, UNIQUE(run_id,callsign), UNIQUE(run_id,crew_id,version),
      FOREIGN KEY(run_id,base_id) REFERENCES aircraft.base(run_id,base_id),
      FOREIGN KEY(run_id,sqn_id) REFERENCES aircraft.squadron(run_id,sqn_id),
      CHECK(flight_hours_total >= 0 AND flight_hours_total NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      CHECK(flight_hours_last_30d >= 0 AND flight_hours_last_30d <= flight_hours_total AND flight_hours_last_30d NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      CHECK(duty_hours_24h >= 0 AND duty_hours_24h <= 24 AND duty_hours_24h NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      CHECK(rest_hours_since_last_duty >= 0 AND rest_hours_since_last_duty NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      CHECK(fatigue_score >= 0 AND fatigue_score <= 1 AND fatigue_score NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)), CHECK(version > 0))""")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.crew_qualification (
      crew_id text NOT NULL REFERENCES crew.crew(crew_id) ON DELETE CASCADE, aircraft_type text NOT NULL REFERENCES aircraft.aircraft_type(type),
      qualification text NOT NULL, PRIMARY KEY(crew_id,aircraft_type,qualification))""")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.duty_interval (
      interval_id uuid PRIMARY KEY, run_id text NOT NULL REFERENCES crew.scenario_run(run_id) ON DELETE CASCADE,
      crew_id text NOT NULL REFERENCES crew.crew(crew_id) ON DELETE CASCADE, start_at timestamptz NOT NULL, end_at timestamptz NULL,
      kind text NOT NULL CHECK(kind IN('duty','rest','sleep','flight')), mission_id text NULL, qualification text NULL,
      is_night boolean NOT NULL DEFAULT false, is_aar boolean NOT NULL DEFAULT false, CHECK(end_at IS NULL OR end_at > start_at),
      CHECK((kind='flight') OR (qualification IS NULL AND NOT is_night AND NOT is_aar)))""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS crew_one_open_interval ON crew.duty_interval(crew_id) WHERE end_at IS NULL")
    op.execute("""CREATE OR REPLACE FUNCTION crew.no_interval_overlap() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF EXISTS(SELECT 1 FROM crew.duty_interval d WHERE d.crew_id=NEW.crew_id AND d.interval_id<>NEW.interval_id
        AND tstzrange(d.start_at,coalesce(d.end_at,'infinity'::timestamptz),'[)') && tstzrange(NEW.start_at,coalesce(NEW.end_at,'infinity'::timestamptz),'[)')) THEN
        RAISE EXCEPTION 'overlapping crew intervals'; END IF; RETURN NEW; END $$""")
    op.execute("DROP TRIGGER IF EXISTS crew_no_interval_overlap ON crew.duty_interval")
    op.execute("CREATE TRIGGER crew_no_interval_overlap BEFORE INSERT OR UPDATE ON crew.duty_interval FOR EACH ROW EXECUTE FUNCTION crew.no_interval_overlap()")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.crew_event_identity (
      event_id uuid PRIMARY KEY, run_id text NOT NULL, crew_id text NOT NULL REFERENCES crew.crew(crew_id) ON DELETE CASCADE,
      version bigint NOT NULL, payload_hash text NOT NULL, logical_time timestamptz NOT NULL, envelope jsonb NOT NULL,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,crew_id,version))""")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.crew_outbox (
      event_id uuid PRIMARY KEY REFERENCES crew.crew_event_identity(event_id) ON DELETE CASCADE, run_id text NOT NULL, crew_id text NOT NULL,
      version bigint NOT NULL, idempotency_key text NOT NULL, command_hash text NOT NULL, event_type text NOT NULL, logical_time timestamptz NOT NULL,
      envelope jsonb NOT NULL, payload_hash text NOT NULL, status_published_at timestamptz NULL, events_published_at timestamptz NULL,
      history_projected_at timestamptz NULL, attempts integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,crew_id,version), UNIQUE(run_id,crew_id,idempotency_key))""")
    op.execute("""CREATE TABLE IF NOT EXISTS crew.crew_duty_log (
      time timestamptz NOT NULL, run_id text NOT NULL, crew_id text NOT NULL, version bigint NOT NULL, event_id uuid NOT NULL REFERENCES crew.crew_event_identity(event_id),
      duty_hours_24h double precision NOT NULL, rest_hours_since_last_duty double precision NOT NULL, flight_hours_last_30d double precision NOT NULL,
      fatigue_score double precision NOT NULL, fatigue_band text NOT NULL, state jsonb NOT NULL, model_version text NOT NULL,
      recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(time,run_id,crew_id,version))""")
    op.execute("SELECT create_hypertable('crew.crew_duty_log','time',chunk_time_interval => interval '1 day',if_not_exists => TRUE)")
    op.execute("CREATE INDEX IF NOT EXISTS crew_state_qualification_gin ON crew.crew USING gin(qualifications)")
    op.execute("CREATE INDEX IF NOT EXISTS crew_state_lookup_idx ON crew.crew(run_id,base_id,role,medical_status)")
    op.execute("CREATE INDEX IF NOT EXISTS crew_interval_lookup_idx ON crew.duty_interval(crew_id,start_at,end_at)")
    op.execute("CREATE INDEX IF NOT EXISTS crew_history_lookup_idx ON crew.crew_duty_log(run_id,crew_id,time DESC,version DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS crew_outbox_pending_idx ON crew.crew_outbox(next_attempt_at,crew_id,version) WHERE status_published_at IS NULL OR events_published_at IS NULL OR history_projected_at IS NULL")
    op.execute("DO $$ BEGIN CREATE ROLE garuda_crew_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("GRANT USAGE ON SCHEMA crew TO garuda_crew_reader")
    op.execute("GRANT SELECT ON ALL TABLES IN SCHEMA crew TO garuda_crew_reader")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA crew GRANT SELECT ON TABLES TO garuda_crew_reader")

def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS crew CASCADE")
