#!/bin/sh
set -eu
: "${AIRSPACE_READER_USER:?AIRSPACE_READER_USER is required}"
: "${AIRSPACE_READER_PASSWORD:?AIRSPACE_READER_PASSWORD is required}"
if ! psql -tAc "SELECT 1 FROM pg_roles WHERE rolname = '$AIRSPACE_READER_USER'" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" | grep -q 1; then
  case "$AIRSPACE_READER_USER" in *[!A-Za-z0-9_]*|'') echo "unsafe AIRSPACE_READER_USER" >&2; exit 1;; esac
  escaped_password=$(printf '%s' "$AIRSPACE_READER_PASSWORD" | sed "s/'/''/g")
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "CREATE ROLE \"$AIRSPACE_READER_USER\" LOGIN PASSWORD '$escaped_password'"
fi
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "GRANT garuda_airspace_reader TO \"$AIRSPACE_READER_USER\"" 2>/dev/null || true
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
DO $$ BEGIN CREATE ROLE garuda_airspace_reader NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "GRANT garuda_airspace_reader TO \"$AIRSPACE_READER_USER\""
