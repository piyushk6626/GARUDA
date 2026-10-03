#!/bin/bash
set -euo pipefail

brokers="${BROKERS:-redpanda:9092}"
retention_ms="${TOPIC_RETENTION_MS:-604800000}"

create_topic() {
    local topic="$1"
    if rpk topic describe "$topic" --brokers "$brokers" >/dev/null 2>&1; then
        echo "topic ${topic} already exists"
        return 0
    fi
    rpk topic create "$topic" \
        --brokers "$brokers" \
        -p 3 \
        -r 1 \
        -c "retention.ms=${retention_ms}"
    echo "created ${topic}"
}

create_topic garuda.mission.request
create_topic garuda.plan.event
