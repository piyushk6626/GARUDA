"""Aircraft authoritative state, event identity, outbox, and history.

Revision ID: 20261004_aircraft
Revises: 20261004_adsb
"""
from alembic import op

revision = "20261004_aircraft"
down_revision = "20261004_adsb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS aircraft")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.scenario_run (
      run_id text PRIMARY KEY CHECK (run_id ~ '^[A-Za-z0-9_-]+$'), seed bigint NOT NULL,
      start_at timestamptz NOT NULL, configuration_hash text NOT NULL, generator_version text NOT NULL,
      logical_clock timestamptz NOT NULL, lifecycle_status text NOT NULL DEFAULT 'seeded'
        CHECK (lifecycle_status IN ('seeded','running','complete','failed')),
      rng_state jsonb NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE (run_id, configuration_hash)
    )""")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.base (
      run_id text NOT NULL REFERENCES aircraft.scenario_run(run_id) ON DELETE CASCADE,
      base_id text NOT NULL, name text NOT NULL, icao char(4) NOT NULL, role text NOT NULL,
      PRIMARY KEY (run_id,base_id))""")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.aircraft_type (
      type text PRIMARY KEY CHECK (type IN ('Su-30MKI','Rafale','Tejas','Mirage-2000','Jaguar','C-130J','IL-78','AEW&C','helicopter')),
      fuel_capacity_kg double precision NOT NULL CHECK (fuel_capacity_kg > 0),
      inspection_cycle_hours double precision NOT NULL CHECK (inspection_cycle_hours > 0),
      turnaround_min integer NOT NULL CHECK (turnaround_min > 0), range_km double precision NOT NULL CHECK (range_km > 0),
      cruise_kts double precision NOT NULL CHECK (cruise_kts > 0), ceiling_ft double precision NOT NULL CHECK (ceiling_ft > 0),
      capabilities text[] NOT NULL DEFAULT '{}')""")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.squadron (
      run_id text NOT NULL, sqn_id text NOT NULL, home_base_id text NOT NULL, type text NOT NULL REFERENCES aircraft.aircraft_type(type),
      name text NOT NULL, role text NOT NULL, PRIMARY KEY (run_id,sqn_id),
      UNIQUE (run_id,sqn_id,type), FOREIGN KEY (run_id,home_base_id) REFERENCES aircraft.base(run_id,base_id))""")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.aircraft (
      tail_no text PRIMARY KEY, run_id text NOT NULL, type text NOT NULL REFERENCES aircraft.aircraft_type(type), sqn_id text NOT NULL,
      base_id text NOT NULL, status text NOT NULL CHECK (status IN ('FMC','PMC','NMC','AOG','IN_MISSION')),
      status_reason text NULL, fuel_kg double precision NOT NULL CHECK (fuel_kg >= 0 AND fuel_kg NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      flight_hours_total double precision NOT NULL CHECK (flight_hours_total >= 0 AND flight_hours_total NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      hours_since_inspection double precision NOT NULL CHECK (hours_since_inspection >= 0 AND hours_since_inspection NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      hours_to_next_inspection double precision NOT NULL CHECK (hours_to_next_inspection >= 0 AND hours_to_next_inspection NOT IN ('NaN'::float8,'Infinity'::float8,'-Infinity'::float8)),
      maint_return_eta timestamptz NULL, capabilities text[] NOT NULL DEFAULT '{}', turnaround_min integer NOT NULL CHECK (turnaround_min > 0),
      status_since timestamptz NOT NULL, ready_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      version bigint NOT NULL CHECK (version > 0), last_event_id uuid NOT NULL UNIQUE,
      UNIQUE (run_id,tail_no,version), FOREIGN KEY (run_id,base_id) REFERENCES aircraft.base(run_id,base_id),
      FOREIGN KEY (run_id,sqn_id,type) REFERENCES aircraft.squadron(run_id,sqn_id,type),
      CHECK ((status <> 'FMC') OR (maint_return_eta IS NULL AND status_reason IS NULL)),
      CHECK ((status NOT IN ('NMC','AOG')) OR status_reason IS NOT NULL),
      CHECK (maint_return_eta IS NULL OR maint_return_eta > updated_at))""")
    op.execute("""CREATE OR REPLACE FUNCTION aircraft.check_fuel_capacity() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE capacity double precision; BEGIN SELECT fuel_capacity_kg INTO capacity FROM aircraft.aircraft_type WHERE type=NEW.type;
      IF NEW.fuel_kg > capacity THEN RAISE EXCEPTION 'fuel exceeds capacity for %', NEW.type USING ERRCODE='23514'; END IF; RETURN NEW; END $$""")
    op.execute("DROP TRIGGER IF EXISTS aircraft_fuel_capacity ON aircraft.aircraft")
    op.execute("CREATE TRIGGER aircraft_fuel_capacity BEFORE INSERT OR UPDATE OF fuel_kg,type ON aircraft.aircraft FOR EACH ROW EXECUTE FUNCTION aircraft.check_fuel_capacity()")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.history_event_identity (
      event_id uuid PRIMARY KEY, run_id text NOT NULL, tail_no text NOT NULL, version bigint NOT NULL,
      payload_hash text NOT NULL, logical_time timestamptz NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE (run_id,tail_no,version))""")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.aircraft_status_history (
      time timestamptz NOT NULL, run_id text NOT NULL, tail_no text NOT NULL, version bigint NOT NULL, event_id uuid NOT NULL,
      status text NOT NULL, status_reason text NULL, base_id text NOT NULL, fuel_kg double precision NOT NULL,
      flight_hours_total double precision NOT NULL, hours_since_inspection double precision NOT NULL, hours_to_next_inspection double precision NOT NULL,
      maint_return_eta timestamptz NULL, capabilities text[] NOT NULL, status_since timestamptz NOT NULL, ready_at timestamptz NOT NULL,
      event_kind text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      PRIMARY KEY(time,run_id,tail_no,version), FOREIGN KEY(event_id) REFERENCES aircraft.history_event_identity(event_id))""")
    op.execute("SELECT create_hypertable('aircraft.aircraft_status_history','time',chunk_time_interval => interval '1 day',if_not_exists => TRUE)")
    op.execute("""CREATE TABLE IF NOT EXISTS aircraft.aircraft_outbox (
      event_id uuid PRIMARY KEY, run_id text NOT NULL, tail_no text NOT NULL, version bigint NOT NULL, event_type text NOT NULL,
      logical_time timestamptz NOT NULL, envelope jsonb NOT NULL, payload_hash text NOT NULL, idempotency_key text NOT NULL,
      status_published_at timestamptz NULL, events_published_at timestamptz NULL, history_projected_at timestamptz NULL,
      retry_count integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(run_id,tail_no,version), UNIQUE(run_id,tail_no,idempotency_key))""")
    op.execute("CREATE INDEX IF NOT EXISTS aircraft_state_base_status_idx ON aircraft.aircraft(run_id,base_id,status)")
    op.execute("CREATE INDEX IF NOT EXISTS aircraft_state_type_status_idx ON aircraft.aircraft(run_id,type,status)")
    op.execute("CREATE INDEX IF NOT EXISTS aircraft_outbox_pending_idx ON aircraft.aircraft_outbox(next_attempt_at) WHERE status_published_at IS NULL OR events_published_at IS NULL OR history_projected_at IS NULL")
    op.execute("CREATE INDEX IF NOT EXISTS aircraft_history_lookup_idx ON aircraft.aircraft_status_history(run_id,tail_no,time DESC,version DESC)")
    op.execute("DO $$ BEGIN CREATE ROLE garuda_aircraft_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("GRANT USAGE ON SCHEMA aircraft TO garuda_aircraft_reader")
    op.execute("GRANT SELECT ON aircraft.scenario_run,aircraft.base,aircraft.aircraft_type,aircraft.squadron,aircraft.aircraft,aircraft.aircraft_status_history TO garuda_aircraft_reader")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS aircraft CASCADE")
