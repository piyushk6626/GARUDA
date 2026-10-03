from __future__ import annotations

import os
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
# A generic DSN lets domain stacks reuse the single migration chain.  ADS-B's
# setting remains the fallback so its existing commands keep their behaviour.
if dsn := os.getenv("GARUDA_POSTGRES_DSN") or os.getenv("ADSB_POSTGRES_DSN"):
    config.set_main_option("sqlalchemy.url", dsn.replace("postgresql://", "postgresql+psycopg://", 1))

if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
