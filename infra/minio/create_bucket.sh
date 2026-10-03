#!/bin/sh
set -eu

endpoint="${MINIO_ENDPOINT:-http://minio:9000}"
export AWS_ACCESS_KEY_ID="${MINIO_ROOT_USER}"
export AWS_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

attempts=0
while [ "$attempts" -lt 30 ]; do
    if aws --endpoint-url "$endpoint" s3 ls "s3://garuda" >/dev/null 2>&1; then
        echo "bucket garuda ready"
        exit 0
    fi
    if aws --endpoint-url "$endpoint" s3 mb "s3://garuda" >/dev/null 2>&1; then
        echo "bucket garuda ready"
        exit 0
    fi
    attempts=$((attempts + 1))
    sleep 2
done

echo "minio did not become ready" >&2
exit 1
