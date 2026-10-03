FROM postgis/postgis:16-3.5

USER root

# PostGIS ships in the base image. TimescaleDB and pgvector are installed here so
# later domains can share this one Postgres without a second database.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        postgresql-16-pgvector \
    && curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/timescaledb.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends timescaledb-2-postgresql-16 \
    && rm -rf /var/lib/apt/lists/*

USER postgres
