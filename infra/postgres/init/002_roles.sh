#!/bin/bash
set -euo pipefail

password="${LLM_RO_PASSWORD:-llm_ro_dev}"

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -v llm_password="$password" <<'EOSQL'
SELECT format('CREATE ROLE llm_ro LOGIN PASSWORD %L', :'llm_password')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'llm_ro')
\gexec

ALTER ROLE llm_ro SET statement_timeout = '5s';
GRANT USAGE ON SCHEMA public TO llm_ro;
EOSQL

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    -c "GRANT CONNECT ON DATABASE \"${POSTGRES_DB}\" TO llm_ro;"
