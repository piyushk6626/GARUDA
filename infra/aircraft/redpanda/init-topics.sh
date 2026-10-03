#!/bin/sh
set -eu
create() {
  topic="$1"; cleanup="$2"
  if ! rpk topic describe "$topic" --brokers redpanda:9092 >/dev/null 2>&1; then
    rpk topic create "$topic" --brokers redpanda:9092 --partitions 3 --replicas 1
  fi
  rpk topic alter-config "$topic" --brokers redpanda:9092 --set cleanup.policy="$cleanup" --set retention.ms=604800000 --set message.timestamp.type=LogAppendTime
}
create garuda.aircraft.status compact,delete
create garuda.aircraft.events delete
create garuda.aircraft.dead_letter delete
