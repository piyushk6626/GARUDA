"""Aircraft, squadrons, and the T0 readiness snapshot."""

from alembic import op

revision = "006_aircraft"
down_revision = "005_gis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE aircraft_type (
            type text PRIMARY KEY CHECK (
                type IN (
                    'Su-30MKI', 'Rafale', 'Tejas', 'Mirage-2000', 'Jaguar',
                    'C-130J', 'IL-78', 'AEW&C', 'helicopter'
                )
            ),
            fuel_capacity_kg real NOT NULL CHECK (fuel_capacity_kg > 0),
            inspection_cycle_hours real NOT NULL CHECK (inspection_cycle_hours > 0),
            turnaround_min integer NOT NULL CHECK (turnaround_min > 0),
            range_km real NOT NULL CHECK (range_km > 0),
            cruise_kts real NOT NULL CHECK (cruise_kts > 0),
            ceiling_ft real NOT NULL CHECK (ceiling_ft > 0),
            capabilities text[] NOT NULL CHECK (cardinality(capabilities) >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE squadron (
            sqn_id text PRIMARY KEY,
            type text NOT NULL REFERENCES aircraft_type (type),
            home_base_id text NOT NULL REFERENCES base (base_id),
            name text NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE aircraft (
            tail_no text PRIMARY KEY,
            type text NOT NULL REFERENCES aircraft_type (type),
            sqn_id text NOT NULL REFERENCES squadron (sqn_id),
            base_id text NOT NULL REFERENCES base (base_id),
            status text NOT NULL CHECK (
                status IN ('FMC', 'PMC', 'NMC', 'AOG', 'IN_MISSION')
            ),
            status_reason text,
            fuel_kg real NOT NULL CHECK (fuel_kg >= 0),
            fuel_capacity_kg real NOT NULL CHECK (fuel_capacity_kg > 0),
            flight_hours_total real NOT NULL CHECK (flight_hours_total >= 0),
            hours_to_next_inspection real NOT NULL CHECK (hours_to_next_inspection >= 0),
            inspection_cycle_hours real NOT NULL CHECK (inspection_cycle_hours > 0),
            maint_return_eta timestamptz,
            turnaround_min integer NOT NULL CHECK (turnaround_min > 0),
            capabilities text[] NOT NULL CHECK (cardinality(capabilities) >= 1),
            range_km real NOT NULL CHECK (range_km > 0),
            cruise_kts real NOT NULL CHECK (cruise_kts > 0),
            ceiling_ft real NOT NULL CHECK (ceiling_ft > 0),
            updated_at timestamptz NOT NULL,
            CHECK (fuel_kg <= fuel_capacity_kg),
            CHECK (hours_to_next_inspection <= inspection_cycle_hours),
            CHECK (
                status <> 'FMC'
                OR (status_reason IS NULL AND maint_return_eta IS NULL)
            ),
            CHECK (status NOT IN ('PMC', 'NMC', 'AOG') OR status_reason IS NOT NULL),
            CHECK (status NOT IN ('NMC', 'AOG') OR maint_return_eta IS NOT NULL),
            CHECK (maint_return_eta IS NULL OR maint_return_eta > updated_at)
        )
        """
    )
    op.execute("CREATE INDEX aircraft_base_status_idx ON aircraft (base_id, status)")
    op.execute(
        """
        CREATE TABLE aircraft_status_history (
            time timestamptz NOT NULL,
            tail_no text NOT NULL REFERENCES aircraft (tail_no),
            status text NOT NULL,
            base_id text NOT NULL,
            fuel_kg real NOT NULL CHECK (fuel_kg >= 0),
            flight_hours_total real NOT NULL CHECK (flight_hours_total >= 0),
            hours_to_next_inspection real NOT NULL CHECK (hours_to_next_inspection >= 0),
            PRIMARY KEY (time, tail_no)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('aircraft_status_history', 'time', if_not_exists => TRUE)"
    )
    op.execute(
        """
        GRANT SELECT ON aircraft_type, squadron, aircraft, aircraft_status_history TO llm_ro
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS aircraft_status_history")
    op.execute("DROP TABLE IF EXISTS aircraft")
    op.execute("DROP TABLE IF EXISTS squadron")
    op.execute("DROP TABLE IF EXISTS aircraft_type")
