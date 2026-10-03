#!/bin/sh
set -eu

attempts=0
until mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"; do
    attempts=$((attempts + 1))
    if [ "$attempts" -gt 30 ]; then
        echo "minio did not become ready" >&2
        exit 1
    fi
    sleep 2
done

mc mb --ignore-existing local/garuda
echo "bucket garuda ready"
