"""Forecast grid and station observations."""

from alembic import op

revision = "004_weather"
down_revision = "003_weapon_loads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE weather_grid (
            time timestamptz NOT NULL,
            lat double precision NOT NULL,
            lon double precision NOT NULL,
            position geometry(Point, 4326) NOT NULL,
            cloud_low_pct real NOT NULL CHECK (cloud_low_pct >= 0 AND cloud_low_pct <= 100),
            cloud_mid_pct real NOT NULL CHECK (cloud_mid_pct >= 0 AND cloud_mid_pct <= 100),
            cloud_high_pct real NOT NULL CHECK (cloud_high_pct >= 0 AND cloud_high_pct <= 100),
            ceiling_ft real CHECK (ceiling_ft IS NULL OR ceiling_ft >= 0),
            visibility_km real NOT NULL CHECK (visibility_km >= 0),
            wind_kts real NOT NULL CHECK (wind_kts >= 0),
            wind_dir_deg real NOT NULL CHECK (wind_dir_deg >= 0 AND wind_dir_deg < 360),
            gust_kts real NOT NULL CHECK (gust_kts >= 0),
            precip_mm_h real NOT NULL CHECK (precip_mm_h >= 0),
            cape_jkg real NOT NULL CHECK (cape_jkg >= 0),
            temp_c real NOT NULL,
            freezing_level_ft real NOT NULL CHECK (freezing_level_ft >= 0),
            source text NOT NULL CHECK (source IN ('synthetic', 'open-meteo', 'metar')),
            issued_at timestamptz NOT NULL,
            flyability real NOT NULL CHECK (flyability >= 0 AND flyability <= 1),
            cb_flag boolean NOT NULL,
            PRIMARY KEY (time, lat, lon, source)
        )
        """
    )
    op.execute("SELECT create_hypertable('weather_grid', 'time', if_not_exists => TRUE)")
    op.execute("CREATE INDEX weather_grid_position_gix ON weather_grid USING gist (position)")
    op.execute(
        "SELECT add_retention_policy('weather_grid', INTERVAL '7 days', if_not_exists => TRUE)"
    )
    op.execute(
        """
        CREATE TABLE weather_station_obs (
            obs_time timestamptz NOT NULL,
            icao text NOT NULL,
            source text NOT NULL CHECK (source IN ('synthetic', 'open-meteo', 'metar')),
            raw_text text NOT NULL,
            flight_category text NOT NULL CHECK (
                flight_category IN ('VFR', 'MVFR', 'IFR', 'LIFR')
            ),
            ceiling_ft real CHECK (ceiling_ft IS NULL OR ceiling_ft >= 0),
            vis_km real NOT NULL CHECK (vis_km >= 0),
            wind_kts real NOT NULL CHECK (wind_kts >= 0),
            crosswind_kts real NOT NULL CHECK (crosswind_kts >= 0),
            flyability real NOT NULL CHECK (flyability >= 0 AND flyability <= 1),
            PRIMARY KEY (obs_time, icao, source)
        )
        """
    )
    op.execute("SELECT create_hypertable('weather_station_obs', 'obs_time', if_not_exists => TRUE)")
    op.execute(
        "SELECT add_retention_policy('weather_station_obs', INTERVAL '30 days', if_not_exists => TRUE)"
    )
    op.execute("GRANT SELECT ON weather_grid, weather_station_obs TO llm_ro")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS weather_station_obs")
    op.execute("DROP TABLE IF EXISTS weather_grid")
