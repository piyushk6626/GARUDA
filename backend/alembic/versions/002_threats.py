"""Threat sites and mobile track history."""

from alembic import op

revision = "002_threats"
down_revision = "001_mission_priorities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE threat (
            threat_id text PRIMARY KEY,
            type text NOT NULL CHECK (
                type IN (
                    'SAM_LONG', 'SAM_MED', 'SAM_SHORT', 'RADAR_EW', 'RADAR_GCI',
                    'AAA', 'ENEMY_CAP', 'AIRFIELD'
                )
            ),
            position geometry(Point, 4326) NOT NULL,
            mobile boolean NOT NULL,
            engagement_range_km real NOT NULL CHECK (engagement_range_km >= 0),
            detection_range_km real NOT NULL CHECK (detection_range_km >= 0),
            min_alt_ft integer NOT NULL,
            max_alt_ft integer NOT NULL,
            pk_base real NOT NULL CHECK (pk_base >= 0 AND pk_base <= 1),
            confidence real NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            first_seen timestamptz NOT NULL,
            last_seen timestamptz NOT NULL,
            status text NOT NULL CHECK (
                status IN ('ACTIVE', 'SUPPRESSED', 'DESTROYED', 'UNKNOWN')
            ),
            CHECK (detection_range_km >= engagement_range_km),
            CHECK (max_alt_ft >= min_alt_ft),
            CHECK (last_seen >= first_seen)
        )
        """
    )
    op.execute("CREATE INDEX threat_position_gix ON threat USING gist (position)")
    op.execute(
        """
        CREATE TABLE threat_track (
            time timestamptz NOT NULL,
            threat_id text NOT NULL REFERENCES threat (threat_id),
            geom geometry(Point, 4326) NOT NULL,
            speed_kts real NOT NULL CHECK (speed_kts > 0),
            heading real NOT NULL CHECK (heading >= 0 AND heading < 360),
            PRIMARY KEY (time, threat_id)
        )
        """
    )
    op.execute("SELECT create_hypertable('threat_track', 'time', if_not_exists => TRUE)")
    op.execute("GRANT SELECT ON threat, threat_track TO llm_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS threat_track")
    op.execute("DROP TABLE IF EXISTS threat")
