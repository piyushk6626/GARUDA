"""One integration case per ADS-B store, executed by the Compose test runner.

These check storage capabilities; the ingestion and repository implementations
remain planned work. Every case owns and cleans up its isolated test resources.
"""

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import boto3
import psycopg
import redis
from botocore.config import Config
from confluent_kafka import Consumer, Producer, TopicPartition
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic
from psycopg import sql


def test_timescaledb_spatial_history():
    """Duplicate-safe positions support spatial queries and materialized buckets."""
    schema = "adsb_test_" + uuid.uuid4().hex
    with psycopg.connect(
        host=os.getenv("ADSB_PG_HOST", "localhost"),
        port=int(os.getenv("ADSB_PG_PORT", "15432")),
        user=os.getenv("ADSB_PG_USER", "garuda"),
        password=os.getenv("ADSB_PG_PASSWORD", "garuda-local-only"),
        dbname=os.getenv("ADSB_PG_DATABASE", "garuda"),
        connect_timeout=5, autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET statement_timeout = '15s'")
            cursor.execute("SELECT extname FROM pg_extension")
            assert {"timescaledb", "postgis"} <= {row[0] for row in cursor.fetchall()}
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                cursor.execute(sql.SQL(
                    "CREATE TABLE {}.track (time TIMESTAMPTZ NOT NULL, "
                    "icao24 TEXT NOT NULL, geom geometry(Point,4326) NOT NULL, "
                    "PRIMARY KEY (time, icao24))"
                ).format(sql.Identifier(schema)))
                cursor.execute(
                    "SELECT create_hypertable(%s::regclass, 'time', "
                    "chunk_time_interval => INTERVAL '1 hour')", (schema + ".track",)
                )
                cursor.execute(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_schema = %s AND hypertable_name = 'track'", (schema,)
                )
                assert cursor.fetchone()[0] == 1
                at = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
                insert = sql.SQL(
                    "INSERT INTO {}.track VALUES (%s, %s, "
                    "ST_SetSRID(ST_MakePoint(%s, %s), 4326)) ON CONFLICT DO NOTHING"
                ).format(sql.Identifier(schema))
                cursor.executemany(insert, [
                    (at, "abc123", 73.0, 26.3),
                    (at, "abc123", 73.0, 26.3),
                    (at + timedelta(minutes=1), "abc123", 73.01, 26.3),
                    (at, "def456", 74.0, 26.3),
                ])
                cursor.execute(sql.SQL("SELECT count(*) FROM {}.track").format(sql.Identifier(schema)))
                assert cursor.fetchone()[0] == 3
                cursor.execute(sql.SQL(
                    "SELECT DISTINCT icao24 FROM {}.track WHERE time >= %s AND time < %s "
                    "AND ST_DWithin(geom::geography, "
                    "ST_SetSRID(ST_MakePoint(%s,%s),4326)::geography,%s)"
                ).format(sql.Identifier(schema)), (at, at + timedelta(minutes=5), 73.0, 26.3, 10000))
                assert cursor.fetchall() == [("abc123",)]
                cursor.execute(sql.SQL(
                    "CREATE MATERIALIZED VIEW {s}.track_5m "
                    "WITH (timescaledb.continuous, timescaledb.materialized_only=true) AS "
                    "SELECT time_bucket(INTERVAL '5 minutes', time) AS bucket, "
                    "icao24, count(*) AS samples FROM {s}.track "
                    "GROUP BY bucket, icao24 WITH NO DATA"
                ).format(s=sql.Identifier(schema)))
                cursor.execute("CALL refresh_continuous_aggregate(%s::regclass, %s::timestamptz, %s::timestamptz)",
                               (schema + ".track_5m", at, at + timedelta(minutes=5)))
                cursor.execute(sql.SQL(
                    "SELECT icao24, bucket, samples FROM {}.track_5m ORDER BY icao24"
                ).format(sql.Identifier(schema)))
                assert cursor.fetchall() == [("abc123", at, 2), ("def456", at, 1)]
            finally:
                cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_redis_live_geo_and_expiry():
    """Per-aircraft payload expiry permits removal without losing fresh traffic."""
    client = redis.Redis(
        host=os.getenv("ADSB_REDIS_HOST", "localhost"),
        port=int(os.getenv("ADSB_REDIS_PORT", "16379")),
        password=os.getenv("ADSB_REDIS_PASSWORD", "garuda-redis-local-only"),
        decode_responses=True, socket_connect_timeout=5, socket_timeout=5,
    )
    prefix = "adsb:test:" + uuid.uuid4().hex
    geo, expiry = prefix + ":live", prefix + ":expires"
    fresh, stale = prefix + ":track:abc123", prefix + ":track:def456"
    try:
        now = time.time()
        with client.pipeline(transaction=True) as transaction:
            transaction.geoadd(geo, [73.0, 26.3, "abc123", 73.01, 26.3, "def456"])
            transaction.set(fresh, json.dumps({"icao24": "abc123", "alt_m": 9000}), ex=120)
            transaction.set(stale, json.dumps({"icao24": "def456", "alt_m": None}), ex=1)
            transaction.zadd(expiry, {"abc123": now + 120, "def456": now + 1})
            transaction.execute()
        assert set(client.geosearch(geo, longitude=73.0, latitude=26.3, radius=10, unit="km")) == {"abc123", "def456"}
        assert 0 < client.ttl(fresh) <= 120
        deadline = time.monotonic() + 5
        while client.exists(stale) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get(stale) is None, "per-aircraft metadata did not expire"
        # Storage primitives only; the domain's version-checked janitor is planned.
        expired = client.zrangebyscore(expiry, "-inf", time.time())
        assert expired == ["def456"]
        with client.pipeline(transaction=True) as transaction:
            transaction.zrem(geo, *expired)
            transaction.zrem(expiry, *expired)
            transaction.execute()
        assert client.geosearch(geo, longitude=73.0, latitude=26.3, radius=10, unit="km") == ["abc123"]
        assert json.loads(client.get(fresh))["alt_m"] == 9000
    finally:
        client.delete(geo, expiry, fresh, stale)
        client.close()


def test_redpanda_position_roundtrip():
    """Validate topic configuration, keyed event delivery, and explicit replay."""
    brokers = os.getenv("ADSB_KAFKA_BROKERS", "localhost:19092")
    admin = AdminClient({"bootstrap.servers": brokers, "socket.timeout.ms": 5000})
    metadata = admin.list_topics(topic="garuda.adsb.positions", timeout=10)
    assert metadata.topics["garuda.adsb.positions"].error is None
    assert len(metadata.topics["garuda.adsb.positions"].partitions) == 3
    resource = ConfigResource(ConfigResource.Type.TOPIC, "garuda.adsb.positions")
    config = admin.describe_configs([resource], request_timeout=10)[resource].result(timeout=15)
    assert config["cleanup.policy"].value == "delete"
    assert config["retention.ms"].value == "604800000"
    topic = "garuda.adsb.test." + uuid.uuid4().hex
    admin.create_topics([
        NewTopic(topic, num_partitions=1, replication_factor=1,
                 config={"cleanup.policy": "delete", "retention.ms": "60000"})
    ], request_timeout=10)[topic].result(timeout=15)
    consumer = None
    try:
        at = datetime(2026, 10, 3, 10, 0, tzinfo=timezone.utc)
        event = {"event_id": str(uuid.uuid4()), "type": "adsb.position.observed",
                 "entity_id": "abc123", "ts": at.isoformat(),
                 "version": int(at.timestamp()), "payload": {"lat": 26.3, "lon": 73.0}}
        deliveries = []
        producer = Producer({"bootstrap.servers": brokers, "enable.idempotence": True,
                             "message.timeout.ms": 10000})
        producer.produce(topic, key="abc123", value=json.dumps(event),
                         on_delivery=lambda error, message: deliveries.append(error))
        assert producer.flush(15) == 0
        assert deliveries == [None]
        consumer = Consumer({"bootstrap.servers": brokers, "group.id": topic,
                             "enable.auto.commit": False, "socket.timeout.ms": 5000})
        consumer.assign([TopicPartition(topic, 0, 0)])
        message = consumer.poll(15)
        assert message is not None, "no event received before timeout"
        assert message.error() is None
        assert message.key() == b"abc123"
        assert json.loads(message.value()) == event
        consumer.seek(TopicPartition(topic, 0, message.offset()))
        replay = consumer.poll(15)
        assert replay is not None and replay.error() is None
        assert replay.value() == message.value()
    finally:
        if consumer is not None:
            consumer.close()
        admin.delete_topics([topic], operation_timeout=10, request_timeout=15)[topic].result(timeout=20)


def test_minio_raw_snapshot_roundtrip():
    """Raw JSON and checksum metadata survive an S3 object round-trip."""
    client = boto3.client(
        "s3", endpoint_url=os.getenv("ADSB_MINIO_ENDPOINT", "http://localhost:19000"),
        aws_access_key_id=os.getenv("ADSB_MINIO_USER", "garuda-local"),
        aws_secret_access_key=os.getenv("ADSB_MINIO_PASSWORD", "garuda-minio-local-only"),
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      connect_timeout=5, read_timeout=5, retries={"total_max_attempts": 2}),
    )
    bucket = "adsb-test-" + uuid.uuid4().hex
    key = "adsb/replay/fixture.json"
    snapshot = json.dumps({"time": 1791021600, "states": []}, sort_keys=True).encode()
    checksum = hashlib.sha256(snapshot).hexdigest()
    client.create_bucket(Bucket=bucket)
    try:
        client.put_object(Bucket=bucket, Key=key, Body=snapshot, ContentType="application/json",
                          Metadata={"sha256": checksum, "source": "test-fixture"})
        result = client.get_object(Bucket=bucket, Key=key)
        with result["Body"] as body:
            restored = body.read()
        assert restored == snapshot
        assert hashlib.sha256(restored).hexdigest() == result["Metadata"]["sha256"]
        assert result["Metadata"]["source"] == "test-fixture"
        assert result["ContentType"] == "application/json"
    finally:
        client.delete_object(Bucket=bucket, Key=key)
        client.delete_bucket(Bucket=bucket)
        client.close()
