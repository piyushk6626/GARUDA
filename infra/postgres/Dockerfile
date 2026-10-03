FROM postgres:16

USER root

# postgis/postgis:16-3.5 publishes amd64 only. This image starts from the
# multi-arch postgres:16 base and installs PostGIS, TimescaleDB, and pgvector
# so the same Compose file runs on Apple Silicon and amd64.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        postgresql-16-postgis-3 \
        postgresql-16-pgvector \
    && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

COPY init/ /docker-entrypoint-initdb.d/
RUN chmod 755 /docker-entrypoint-initdb.d/002_roles.sh

USER postgres
