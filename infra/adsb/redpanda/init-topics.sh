#!/bin/sh
set -eu
for topic in garuda.adsb.positions garuda.adsb.dead_letter; do
    if ! rpk topic describe "$topic" --brokers redpanda:9092 >/dev/null 2>&1; then
        rpk topic create "$topic" --brokers redpanda:9092 --partitions 3 --replicas 1
    fi
    rpk topic alter-config "$topic" --brokers redpanda:9092 \
        --set cleanup.policy=delete --set retention.ms=604800000
done
