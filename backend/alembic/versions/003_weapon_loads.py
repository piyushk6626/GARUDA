"""Weapon catalog, stock, load configs, and station compatibility."""

from alembic import op

revision = "003_weapon_loads"
down_revision = "002_threats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE weapon_type (
            weapon_code text PRIMARY KEY,
            category text NOT NULL CHECK (
                category IN (
                    'AAM_BVR', 'AAM_WVR', 'ASM', 'LGB', 'UNGUIDED',
                    'ANTI_RADIATION', 'ANTI_SHIP', 'PODS'
                )
            ),
            range_km real NOT NULL CHECK (range_km >= 0),
            weight_kg real NOT NULL CHECK (weight_kg > 0),
            pk_by_target_class jsonb NOT NULL,
            min_release_alt_ft integer NOT NULL CHECK (min_release_alt_ft >= 0),
            guidance text NOT NULL CHECK (
                guidance IN ('RADAR', 'IR', 'GPS', 'LASER', 'OPTICAL', 'NONE')
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE weapon_stock (
            base_id text NOT NULL,
            weapon_code text NOT NULL REFERENCES weapon_type (weapon_code),
            quantity integer NOT NULL CHECK (quantity >= 0),
            reserved_qty integer NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (base_id, weapon_code),
            CHECK (reserved_qty <= quantity)
        )
        """
    )
    op.execute("CREATE INDEX weapon_stock_base_idx ON weapon_stock (base_id)")
    op.execute(
        """
        CREATE TABLE load_config (
            config_id text PRIMARY KEY,
            aircraft_type text NOT NULL,
            role text NOT NULL CHECK (role IN ('DCA', 'STRIKE', 'SEAD', 'CAS', 'ISR')),
            stations jsonb NOT NULL,
            total_weight_kg real NOT NULL CHECK (total_weight_kg > 0),
            range_penalty_pct real NOT NULL CHECK (
                range_penalty_pct >= 0 AND range_penalty_pct <= 15
            )
        )
        """
    )
    op.execute("CREATE INDEX load_config_aircraft_idx ON load_config (aircraft_type)")
    op.execute(
        """
        CREATE TABLE weapon_compatibility (
            aircraft_type text NOT NULL,
            weapon_code text NOT NULL REFERENCES weapon_type (weapon_code),
            allowed_stations text[] NOT NULL CHECK (cardinality(allowed_stations) >= 1),
            PRIMARY KEY (aircraft_type, weapon_code)
        )
        """
    )
    op.execute(
        """
        GRANT SELECT ON weapon_type, weapon_stock, load_config, weapon_compatibility
        TO llm_ro
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weapon_compatibility")
    op.execute("DROP TABLE IF EXISTS load_config")
    op.execute("DROP TABLE IF EXISTS weapon_stock")
    op.execute("DROP TABLE IF EXISTS weapon_type")
