#!/bin/sh
set -eu
for topic in garuda.airspace.activation garuda.airspace.dead_letter; do
  rpk topic create "$topic" --brokers redpanda:9092 --partitions 3 --replicas 1 2>/dev/null || true
  rpk topic alter-config "$topic" --brokers redpanda:9092 --set cleanup.policy=delete --set retention.ms=604800000 --set message.timestamp.type=LogAppendTime
done
