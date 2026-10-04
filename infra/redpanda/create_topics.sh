#!/bin/bash
set -euo pipefail

brokers="${BROKERS:-redpanda:9092}"
retention_ms="${TOPIC_RETENTION_MS:-604800000}"

create_topic() {
    local topic="$1"
    local policy="${2:-delete}"
    if rpk topic describe "$topic" --brokers "$brokers" >/dev/null 2>&1; then
        echo "topic ${topic} already exists"
        return 0
    fi
    rpk topic create "$topic" \
        --brokers "$brokers" \
        -p 3 \
        -r 1 \
        -c "retention.ms=${retention_ms}" \
        -c "cleanup.policy=${policy}"
    echo "created ${topic}"
}

create_topic garuda.mission.request
create_topic garuda.plan.event
create_topic garuda.threat.update compact
create_topic garuda.weapons.stock
create_topic garuda.weather.update
create_topic garuda.aircraft.status compact
create_topic garuda.crew.status
create_topic garuda.airspace.activation
create_topic garuda.adsb.positions
