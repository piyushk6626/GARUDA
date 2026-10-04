FROM timescale/timescaledb-ha:pg17.6-ts2.22.1@sha256:8cfcb83ee65178270a7b4f1eaf4af0854fa1639ed21c3202fb669707913faed9
COPY infra/airspace/postgres/init/020_extensions.sql /docker-entrypoint-initdb.d/020_extensions.sql
COPY infra/airspace/postgres/init/030_reader.sh /docker-entrypoint-initdb.d/030_reader.sh
