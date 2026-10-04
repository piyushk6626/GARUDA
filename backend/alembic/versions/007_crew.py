"""Crew roster and duty log."""

from alembic import op

revision = "007_crew"
down_revision = "006_aircraft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE crew (
            crew_id text PRIMARY KEY,
            callsign text NOT NULL,
            role text NOT NULL CHECK (role IN ('PILOT', 'WSO', 'LOADMASTER', 'AEW_OP')),
            sqn_id text NOT NULL REFERENCES squadron (sqn_id),
            base_id text NOT NULL REFERENCES base (base_id),
            primary_type text NOT NULL REFERENCES aircraft_type (type),
            qualifications jsonb NOT NULL,
            currency jsonb NOT NULL,
            flight_hours_total real NOT NULL CHECK (flight_hours_total >= 0),
            flight_hours_last_30d real NOT NULL CHECK (flight_hours_last_30d >= 0),
            duty_hours_24h real NOT NULL CHECK (duty_hours_24h >= 0 AND duty_hours_24h <= 24),
            rest_hours_since_last_duty real NOT NULL CHECK (rest_hours_since_last_duty >= 0),
            fatigue_score real NOT NULL CHECK (fatigue_score >= 0 AND fatigue_score <= 1),
            medical_status text NOT NULL CHECK (
                medical_status IN ('FIT', 'LIMITED', 'GROUNDED')
            ),
            leave_until timestamptz,
            current_assignment text REFERENCES mission (mission_id),
            is_flight_lead boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL,
            CHECK (flight_hours_last_30d <= flight_hours_total),
            CHECK (
                (duty_hours_24h > 0 AND rest_hours_since_last_duty = 0)
                OR (duty_hours_24h = 0 AND rest_hours_since_last_duty >= 12)
            )
        )
        """
    )
    op.execute("CREATE INDEX crew_qualifications_gin ON crew USING gin (qualifications)")
    op.execute("CREATE INDEX crew_base_role_idx ON crew (base_id, role)")
    op.execute(
        """
        CREATE TABLE crew_duty_log (
            time timestamptz NOT NULL,
            crew_id text NOT NULL REFERENCES crew (crew_id),
            duty_hours_24h real NOT NULL CHECK (duty_hours_24h >= 0 AND duty_hours_24h <= 24),
            fatigue_score real NOT NULL CHECK (fatigue_score >= 0 AND fatigue_score <= 1),
            PRIMARY KEY (time, crew_id)
        )
        """
    )
    op.execute("SELECT create_hypertable('crew_duty_log', 'time', if_not_exists => TRUE)")
    op.execute("GRANT SELECT ON crew, crew_duty_log TO llm_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS crew_duty_log")
    op.execute("DROP TABLE IF EXISTS crew")
