"""Bases, borders, geographic features, and base-to-target distances."""

from alembic import op

revision = "005_gis"
down_revision = "004_weather"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE base (
            base_id text PRIMARY KEY,
            name text NOT NULL,
            icao text NOT NULL UNIQUE,
            position geometry(Point, 4326) NOT NULL,
            elevation_ft integer NOT NULL,
            runway_count integer NOT NULL CHECK (runway_count >= 1),
            runway_length_m integer NOT NULL CHECK (runway_length_m > 0),
            shelter_count integer NOT NULL CHECK (shelter_count >= 0),
            fuel_capacity_l integer NOT NULL CHECK (fuel_capacity_l >= 0),
            ground_crew_teams integer NOT NULL CHECK (ground_crew_teams >= 0),
            max_sorties_per_hour real NOT NULL CHECK (max_sorties_per_hour > 0)
        )
        """
    )
    op.execute("CREATE INDEX base_position_gix ON base USING gist (position)")
    op.execute(
        """
        CREATE TABLE border (
            border_id text PRIMARY KEY,
            name text NOT NULL,
            kind text NOT NULL CHECK (kind IN ('international', 'coastline', 'admin')),
            geom geometry(MultiLineString, 4326) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX border_geom_gix ON border USING gist (geom)")
    op.execute(
        """
        CREATE TABLE geo_feature (
            feature_id text PRIMARY KEY,
            kind text NOT NULL CHECK (kind IN ('road', 'city', 'river', 'water')),
            name text NOT NULL,
            geom geometry(Geometry, 4326) NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX geo_feature_geom_gix ON geo_feature USING gist (geom)")
    op.execute(
        """
        CREATE TABLE base_target_distance (
            base_id text NOT NULL REFERENCES base (base_id),
            target_id text NOT NULL REFERENCES target (target_id),
            distance_km double precision NOT NULL CHECK (distance_km >= 0),
            PRIMARY KEY (base_id, target_id)
        )
        """
    )
    op.execute(
        """
        ALTER TABLE weapon_stock
            ADD CONSTRAINT weapon_stock_base_id_fkey
            FOREIGN KEY (base_id) REFERENCES base (base_id)
            NOT VALID
        """
    )
    op.execute(
        """
        GRANT SELECT ON base, border, geo_feature, base_target_distance TO llm_ro
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE weapon_stock DROP CONSTRAINT IF EXISTS weapon_stock_base_id_fkey"
    )
    op.execute("DROP TABLE IF EXISTS base_target_distance")
    op.execute("DROP TABLE IF EXISTS geo_feature")
    op.execute("DROP TABLE IF EXISTS border")
    op.execute("DROP TABLE IF EXISTS base")
