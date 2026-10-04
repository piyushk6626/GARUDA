#!/bin/sh
set -eu
create () { rpk topic create "$1" --brokers redpanda:9092 --partitions 3 --replicas 1 2>/dev/null || true; rpk topic alter-config "$1" --brokers redpanda:9092 --set cleanup.policy="$2" --set retention.ms=604800000 --set message.timestamp.type=LogAppendTime; }
create garuda.crew.status compact
create garuda.crew.events delete
create garuda.crew.dead_letter delete
