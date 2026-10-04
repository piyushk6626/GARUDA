"""ADS-B track history."""

from alembic import op

revision = "009_adsb"
down_revision = "008_airspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE adsb_track (
            time timestamptz NOT NULL,
            source_id text NOT NULL,
            icao24 text NOT NULL CHECK (icao24 ~ '^[0-9a-f]{6}$'),
            callsign text,
            origin_country text NOT NULL,
            last_contact timestamptz NOT NULL,
            lat double precision NOT NULL CHECK (lat >= -90 AND lat <= 90),
            lon double precision NOT NULL CHECK (lon >= -180 AND lon <= 180),
            geom geometry(Point, 4326) NOT NULL,
            alt_m real,
            geo_alt_m real,
            speed_ms real CHECK (speed_ms IS NULL OR speed_ms >= 0),
            heading real CHECK (heading IS NULL OR (heading >= 0 AND heading < 360)),
            vrate real,
            on_ground boolean NOT NULL,
            position_source integer NOT NULL CHECK (position_source >= 0 AND position_source <= 3),
            PRIMARY KEY (time, source_id, icao24)
        )
        """
    )
    op.execute("SELECT create_hypertable('adsb_track', 'time', if_not_exists => TRUE)")
    op.execute("CREATE INDEX adsb_track_geom_gix ON adsb_track USING gist (geom)")
    op.execute(
        """
        CREATE VIEW adsb_track_5m AS
        SELECT
            time_bucket('5 minutes', time) AS bucket,
            source_id,
            icao24,
            count(*)::integer AS samples,
            min(alt_m) AS alt_min_m,
            max(alt_m) AS alt_max_m,
            avg(speed_ms) AS speed_ms_mean
        FROM adsb_track
        GROUP BY bucket, source_id, icao24
        """
    )
    op.execute("GRANT SELECT ON adsb_track, adsb_track_5m TO llm_ro")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS adsb_track_5m")
    op.execute("DROP TABLE IF EXISTS adsb_track")
