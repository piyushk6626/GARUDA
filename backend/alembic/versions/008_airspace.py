"""Airspace zones."""

from alembic import op

revision = "008_airspace"
down_revision = "007_crew"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE airspace_zone (
            zone_id text PRIMARY KEY,
            name text NOT NULL,
            type text NOT NULL CHECK (
                type IN (
                    'PROHIBITED', 'RESTRICTED', 'DANGER', 'CTR', 'TMA',
                    'ADIZ', 'CAP', 'AAR_TRACK', 'CORRIDOR', 'TRA'
                )
            ),
            geometry geometry(Polygon, 4326) NOT NULL,
            floor_ft integer NOT NULL,
            ceiling_ft integer NOT NULL,
            active_from timestamptz,
            active_to timestamptz,
            controlling_authority text NOT NULL,
            source text NOT NULL CHECK (source IN ('openaip', 'synthetic')),
            capacity_per_hour integer CHECK (capacity_per_hour IS NULL OR capacity_per_hour >= 0),
            width_km real CHECK (width_km IS NULL OR width_km > 0),
            mission_id text REFERENCES mission (mission_id),
            CHECK (floor_ft <= ceiling_ft),
            CHECK (
                (active_from IS NULL AND active_to IS NULL)
                OR (active_from IS NOT NULL AND active_to IS NOT NULL AND active_from < active_to)
            )
        )
        """
    )
    op.execute("CREATE INDEX airspace_zone_geometry_gix ON airspace_zone USING gist (geometry)")
    op.execute("CREATE INDEX airspace_zone_type_idx ON airspace_zone (type)")
    op.execute("GRANT SELECT ON airspace_zone TO llm_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS airspace_zone")
