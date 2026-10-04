"""Authoritative PostGIS airspace state, schedules, provenance and outbox.

Revision ID: 20261004_airspace
Revises: 20261004_aircraft
"""
from alembic import op
revision="20261004_airspace"; down_revision="20261004_aircraft"; branch_labels=None; depends_on=None

def upgrade()->None:
    op.execute("CREATE SCHEMA IF NOT EXISTS airspace")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.scenario_run (
      run_id text PRIMARY KEY CHECK(run_id ~ '^[A-Za-z0-9_-]+$'), seed bigint NOT NULL, start_at timestamptz NOT NULL,
      logical_clock timestamptz NOT NULL, configuration_hash text NOT NULL, generator_version text NOT NULL,
      input_checksums jsonb NOT NULL DEFAULT '{}', lifecycle_status text NOT NULL DEFAULT 'seeded' CHECK(lifecycle_status IN('seeded','running','complete','failed')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,configuration_hash))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.airspace_zone (
      zone_id text PRIMARY KEY, run_id text NOT NULL REFERENCES airspace.scenario_run(run_id) ON DELETE CASCADE,
      source_identifier text NULL, name text NOT NULL, type text NOT NULL CHECK(type IN('PROHIBITED','RESTRICTED','DANGER','CTR','TMA','ADIZ','CAP','AAR_TRACK','CORRIDOR','TRA')),
      geometry geometry(Geometry,4326) NOT NULL, centerline geometry(LineString,4326) NULL, width_km numeric NULL CHECK(width_km IS NULL OR width_km>0),
      floor_ft integer NULL, ceiling_ft integer NULL, altitude_status text NOT NULL DEFAULT 'known', active_from timestamptz NULL, active_to timestamptz NULL,
      controlling_authority text NOT NULL, source text NOT NULL CHECK(source IN('synthetic','openaip','fabricated')), capacity_per_hour integer NULL CHECK(capacity_per_hour>=0),
      enabled boolean NOT NULL DEFAULT true, version bigint NOT NULL CHECK(version>0), last_event_id uuid NOT NULL UNIQUE, provenance jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL,
      UNIQUE(run_id,source,source_identifier), UNIQUE(run_id,zone_id,version), CHECK(ST_SRID(geometry)=4326 AND NOT ST_IsEmpty(geometry) AND ST_IsValid(geometry) AND GeometryType(geometry) IN ('POLYGON','MULTIPOLYGON')),
      CHECK(centerline IS NULL OR ST_SRID(centerline)=4326), CHECK(floor_ft IS NULL OR ceiling_ft IS NULL OR floor_ft<=ceiling_ft),
      CHECK((active_from IS NULL AND active_to IS NULL) OR (active_from IS NOT NULL AND active_to IS NOT NULL AND active_from<active_to)))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.zone_activation_window (
      window_id uuid PRIMARY KEY, zone_id text NOT NULL REFERENCES airspace.airspace_zone(zone_id) ON DELETE CASCADE,
      active_from timestamptz NOT NULL, active_to timestamptz NOT NULL, mission_id text NULL, version bigint NOT NULL CHECK(version>0),
      UNIQUE(zone_id,active_from,active_to), CHECK(active_from<active_to))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.mission_zone_link (
      run_id text NOT NULL REFERENCES airspace.scenario_run(run_id) ON DELETE CASCADE, mission_id text NOT NULL, zone_id text NOT NULL REFERENCES airspace.airspace_zone(zone_id) ON DELETE CASCADE,
      purpose text NOT NULL, resolution_status text NOT NULL DEFAULT 'unresolved' CHECK(resolution_status IN('unresolved','resolved')), version bigint NOT NULL CHECK(version>0),
      PRIMARY KEY(run_id,mission_id,zone_id))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.import_batch (
      batch_id uuid PRIMARY KEY, run_id text NOT NULL REFERENCES airspace.scenario_run(run_id) ON DELETE CASCADE, source text NOT NULL, country text NOT NULL,
      bbox jsonb NOT NULL, input_hash text NOT NULL, manifest_key text NOT NULL, normalization_version text NOT NULL, accepted_count integer NOT NULL DEFAULT 0, rejected_count integer NOT NULL DEFAULT 0,
      status text NOT NULL CHECK(status IN('staged','complete','failed')), created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,source,input_hash))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.airspace_event (
      event_id uuid PRIMARY KEY, run_id text NOT NULL, zone_id text NOT NULL REFERENCES airspace.airspace_zone(zone_id) ON DELETE CASCADE, version bigint NOT NULL,
      event_type text NOT NULL, payload_hash text NOT NULL, logical_time timestamptz NOT NULL, envelope jsonb NOT NULL, recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(run_id,zone_id,version))""")
    op.execute("""CREATE TABLE IF NOT EXISTS airspace.airspace_outbox (
      event_id uuid PRIMARY KEY REFERENCES airspace.airspace_event(event_id) ON DELETE CASCADE, run_id text NOT NULL, zone_id text NOT NULL, version bigint NOT NULL,
      event_type text NOT NULL, logical_time timestamptz NOT NULL, envelope jsonb NOT NULL, payload_hash text NOT NULL, idempotency_key text NOT NULL,
      published_at timestamptz NULL, retry_count integer NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL DEFAULT clock_timestamp(), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE(run_id,zone_id,version), UNIQUE(run_id,zone_id,idempotency_key))""")
    op.execute("CREATE INDEX IF NOT EXISTS airspace_zone_geometry_gix ON airspace.airspace_zone USING gist(geometry)")
    op.execute("CREATE INDEX IF NOT EXISTS airspace_window_interval_idx ON airspace.zone_activation_window(zone_id,active_from,active_to)")
    op.execute("CREATE INDEX IF NOT EXISTS airspace_zone_run_type_idx ON airspace.airspace_zone(run_id,type)")
    op.execute("CREATE INDEX IF NOT EXISTS airspace_link_lookup_idx ON airspace.mission_zone_link(run_id,mission_id)")
    op.execute("CREATE INDEX IF NOT EXISTS airspace_outbox_pending_idx ON airspace.airspace_outbox(next_attempt_at,zone_id,version) WHERE published_at IS NULL")
    op.execute("DO $$ BEGIN CREATE ROLE garuda_airspace_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("GRANT USAGE ON SCHEMA airspace TO garuda_airspace_reader")
    op.execute("GRANT SELECT ON airspace.scenario_run,airspace.airspace_zone,airspace.zone_activation_window,airspace.mission_zone_link,airspace.import_batch TO garuda_airspace_reader")

def downgrade()->None: op.execute("DROP SCHEMA IF EXISTS airspace CASCADE")
