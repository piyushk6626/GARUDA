"""Mission, target, and versioned plan tables."""

from alembic import op

revision = "001_mission_priorities"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_ro') THEN
                CREATE ROLE llm_ro LOGIN PASSWORD 'llm_ro_dev';
            END IF;
        END
        $$
        """
    )
    op.execute("ALTER ROLE llm_ro SET statement_timeout = '5s'")
    op.execute(
        """
        CREATE TABLE target (
            target_id text PRIMARY KEY,
            class text NOT NULL CHECK (
                class IN ('RUNWAY', 'RADAR', 'BRIDGE', 'ARMOUR', 'C2', 'FUEL_DEPOT')
            ),
            position geometry(Point, 4326) NOT NULL,
            value real NOT NULL,
            hardness real NOT NULL CHECK (hardness >= 0 AND hardness <= 1),
            defended_by text[] NOT NULL DEFAULT '{}'
        )
        """
    )
    op.execute("CREATE INDEX target_position_gix ON target USING gist (position)")
    op.execute(
        """
        CREATE TABLE mission (
            mission_id text PRIMARY KEY,
            type text NOT NULL CHECK (
                type IN (
                    'DCA', 'CAP', 'STRIKE', 'SEAD', 'CAS', 'ISR', 'AAR', 'AIRLIFT', 'SAR'
                )
            ),
            priority integer NOT NULL CHECK (priority BETWEEN 1 AND 5),
            value real NOT NULL,
            requested_by text NOT NULL,
            window_start timestamptz NOT NULL,
            window_end timestamptz NOT NULL,
            duration_min integer NOT NULL CHECK (duration_min > 0),
            target_id text REFERENCES target (target_id),
            area geometry(Polygon, 4326),
            required_capabilities text[] NOT NULL DEFAULT '{}',
            min_aircraft integer NOT NULL CHECK (min_aircraft > 0),
            max_aircraft integer NOT NULL CHECK (max_aircraft >= min_aircraft),
            required_effect text NOT NULL CHECK (
                required_effect IN ('DESTROY', 'SUPPRESS', 'OBSERVE', 'DENY')
            ),
            min_pk real NOT NULL CHECK (min_pk >= 0 AND min_pk <= 1),
            status text NOT NULL CHECK (
                status IN (
                    'REQUESTED', 'PLANNED', 'APPROVED', 'EXECUTING', 'DONE', 'CANCELLED'
                )
            ),
            dependencies text[] NOT NULL DEFAULT '{}',
            CHECK (window_end > window_start),
            CHECK (type NOT IN ('CAP', 'AAR') OR target_id IS NULL)
        )
        """
    )
    op.execute("CREATE INDEX mission_area_gix ON mission USING gist (area)")
    op.execute("CREATE INDEX mission_status_priority_idx ON mission (status, priority)")
    op.execute("CREATE INDEX mission_window_idx ON mission (window_start, window_end)")
    op.execute(
        """
        CREATE TABLE plan (
            plan_id text NOT NULL,
            version integer NOT NULL CHECK (version > 0),
            created_by text NOT NULL,
            objective text,
            status text NOT NULL CHECK (
                status IN ('DRAFT', 'PROPOSED', 'APPROVED', 'REJECTED', 'SUPERSEDED')
            ),
            kpis jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (plan_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE plan_assignment (
            assignment_id text PRIMARY KEY,
            plan_id text NOT NULL,
            version integer NOT NULL,
            mission_id text NOT NULL REFERENCES mission (mission_id),
            tail_no text,
            load_config_id text,
            sortie_no integer,
            depart_time timestamptz,
            tot timestamptz,
            FOREIGN KEY (plan_id, version) REFERENCES plan (plan_id, version)
        )
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO llm_ro")
    op.execute(
        """
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT ON DATABASE %I TO llm_ro', current_database());
        END
        $$
        """
    )
    op.execute("GRANT SELECT ON target, mission, plan, plan_assignment TO llm_ro")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO llm_ro"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS plan_assignment")
    op.execute("DROP TABLE IF EXISTS plan")
    op.execute("DROP TABLE IF EXISTS mission")
    op.execute("DROP TABLE IF EXISTS target")
