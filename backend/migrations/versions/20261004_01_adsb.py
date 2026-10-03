"""ADS-B authoritative track, aggregates, density, and read-only role.

Revision ID: 20261004_adsb
Revises:
"""
from alembic import op

revision = "20261004_adsb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS adsb")
    op.execute("""CREATE TABLE IF NOT EXISTS adsb.adsb_track (
      time timestamptz NOT NULL, source_id text NOT NULL, icao24 char(6) NOT NULL,
      callsign text NULL, origin_country text NULL, last_contact timestamptz NULL,
      lat double precision NOT NULL CHECK (lat BETWEEN -90 AND 90),
      lon double precision NOT NULL CHECK (lon BETWEEN -180 AND 180),
      geom geometry(Point,4326) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon,lat),4326)) STORED,
      alt_m double precision NULL, geo_alt_m double precision NULL,
      speed_ms double precision NULL CHECK (speed_ms >= 0),
      heading double precision NULL CHECK (heading >= 0 AND heading < 360),
      vrate double precision NULL, on_ground boolean NOT NULL,
      position_source smallint NULL, ingested_at timestamptz NOT NULL,
      snapshot_key text NULL, PRIMARY KEY (time, source_id, icao24),
      CHECK (icao24 ~ '^[0-9a-f]{6}$')
    )""")
    op.execute("SELECT create_hypertable('adsb.adsb_track', 'time', chunk_time_interval => interval '1 hour', if_not_exists => TRUE)")
    op.execute("CREATE INDEX IF NOT EXISTS adsb_track_source_aircraft_time_idx ON adsb.adsb_track (source_id, icao24, time DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS adsb_track_geography_idx ON adsb.adsb_track USING gist ((geom::geography))")
    op.execute("""CREATE MATERIALIZED VIEW IF NOT EXISTS adsb.adsb_track_5m
      WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
      SELECT time_bucket('5 minutes', time) AS bucket, source_id, icao24,
        count(*) AS sample_count, min(alt_m) AS min_alt_m, max(alt_m) AS max_alt_m,
        avg(speed_ms) AS mean_speed_ms
      FROM adsb.adsb_track GROUP BY bucket, source_id, icao24 WITH NO DATA""")
    op.execute("""CREATE TABLE IF NOT EXISTS adsb.grid_occupancy (
      hour timestamptz NOT NULL, source_id text NOT NULL, cell_id text NOT NULL,
      icao24 char(6) NOT NULL, PRIMARY KEY(hour, source_id, cell_id, icao24))""")
    op.execute("CREATE INDEX IF NOT EXISTS grid_occupancy_lookup_idx ON adsb.grid_occupancy(source_id, hour DESC, cell_id)")
    op.execute("""CREATE OR REPLACE FUNCTION adsb.record_grid_occupancy() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE grid double precision := 0.05;
      BEGIN IF NOT NEW.on_ground THEN INSERT INTO adsb.grid_occupancy(hour,source_id,cell_id,icao24)
        VALUES (date_trunc('hour',NEW.time), NEW.source_id,
          floor(NEW.lat/grid)::bigint::text || ':' || floor(NEW.lon/grid)::bigint::text, NEW.icao24)
        ON CONFLICT DO NOTHING; END IF; RETURN NEW; END $$""")
    op.execute("DROP TRIGGER IF EXISTS adsb_grid_occupancy_insert ON adsb.adsb_track")
    op.execute("CREATE TRIGGER adsb_grid_occupancy_insert AFTER INSERT ON adsb.adsb_track FOR EACH ROW EXECUTE FUNCTION adsb.record_grid_occupancy()")
    op.execute("SELECT add_retention_policy('adsb.adsb_track', interval '24 hours', if_not_exists => TRUE)")
    op.execute("SELECT add_retention_policy('adsb.adsb_track_5m', interval '30 days', if_not_exists => TRUE)")
    op.execute("SELECT add_continuous_aggregate_policy('adsb.adsb_track_5m', start_offset => interval '23 hours', end_offset => interval '5 minutes', schedule_interval => interval '1 minute', if_not_exists => TRUE)")
    op.execute("DO $$ BEGIN CREATE ROLE garuda_adsb_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$")
    op.execute("GRANT USAGE ON SCHEMA adsb TO garuda_adsb_reader")
    op.execute("GRANT SELECT ON adsb.adsb_track, adsb.adsb_track_5m, adsb.grid_occupancy TO garuda_adsb_reader")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS adsb.adsb_track_5m CASCADE")
    op.execute("DROP TABLE IF EXISTS adsb.grid_occupancy")
    op.execute("DROP TABLE IF EXISTS adsb.adsb_track")
