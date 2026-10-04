"""Retention policies for history hypertables."""

from alembic import op

revision = "010_retention"
down_revision = "009_adsb"
branch_labels = None
depends_on = None

POLICIES = (
    ("aircraft_status_history", "90 days"),
    ("crew_duty_log", "90 days"),
    ("adsb_track", "24 hours"),
)


def upgrade() -> None:
    for table, interval in POLICIES:
        op.execute(f"SELECT add_retention_policy('{table}', INTERVAL '{interval}', if_not_exists => TRUE)")


def downgrade() -> None:
    for table, _ in POLICIES:
        op.execute(f"SELECT remove_retention_policy('{table}', if_exists => TRUE)")
