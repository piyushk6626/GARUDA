"""Idempotently provision the narrow SQL reader login after migrations."""
from __future__ import annotations
import asyncio, os, re
import asyncpg
async def main() -> None:
    dsn=os.environ["GARUDA_POSTGRES_DSN"]; user=os.getenv("CREW_READER_USER","garuda_crew_reader_login"); password=os.getenv("CREW_READER_PASSWORD","change-this-reader-password")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}",user):raise ValueError("invalid reader user")
    conn=await asyncpg.connect(dsn)
    try:
        escaped=password.replace("'","''")
        await conn.execute(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{user}') THEN CREATE ROLE {user} LOGIN PASSWORD '{escaped}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT; END IF; END $$")
        await conn.execute(f"GRANT garuda_crew_reader TO {user}");await conn.execute(f"ALTER ROLE {user} SET statement_timeout = '5s'")
    finally:await conn.close()
if __name__=="__main__":asyncio.run(main())
