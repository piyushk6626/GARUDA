from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from backend.app.config import AdsBSettings, BoundingBox
from backend.app.data.adapters import conflict_candidates
from backend.app.data.archive import MinioSnapshotArchive, SnapshotManifest
from backend.app.data.cache import RedisLiveCache
from backend.app.data.consumers.adsb_history import HistoryConsumer
from backend.app.data.ingestors.adsb import API_URL, TOKEN_URL, OpenSkyClient, OpenSkyError
from backend.app.data.replay.adsb import build_replay
from backend.app.schemas.adsb import PositionObservation, normalize_opensky_response

UTC = timezone.utc
FIXTURE = Path("tests/fixtures/adsb/fabricated_opensky.json")


def observation(version: int = 100, *, icao: str = "abc123", lon: float = 73, lat: float = 26.3) -> PositionObservation:
    return PositionObservation(icao24=icao, time=datetime.fromtimestamp(version, UTC), source_id="test",
        lon=lon, lat=lat, on_ground=False, ingested_at=datetime.fromtimestamp(200, UTC))


def test_normalization_handles_nullable_malformed_and_deterministic_identity():
    payload = {"time": 100, "states": [["ABC123", " CALL ", "X", 100, None, 73, 26, None, False, None, None, None, None, None, None, False, 0],
        ["bogus", None, None, 100, None, 1, 2], ["deadbe", None, None, None, None, 1, 2], "not-a-row"]}
    result = normalize_opensky_response(payload, source_id="opensky", ingested_at=datetime.fromtimestamp(101, UTC))
    assert len(result.observations) == 1 and result.malformed_rows == 2 and result.excluded_rows == 1
    item = result.observations[0]
    assert item.callsign == "CALL" and item.alt_m is None and item.to_event().event_id == item.to_event().event_id


def test_replay_is_deterministic_and_isolated():
    args = dict(replay_id="case-1", start=datetime(2026, 1, 1, tzinfo=UTC), seed=4,
                bbox=BoundingBox(lamin=20, lomin=70, lamax=30, lomax=80), background_count=3)
    first, second = build_replay(FIXTURE, **args), build_replay(FIXTURE, **args)
    assert first.events == second.events
    assert first.source_id == "replay:case-1" and "FABRICATED" in first.label
    assert all(event.payload["replay_id"] == "case-1" for event in first.events)


class FakeRedis:
    def __init__(self): self.geo = {}; self.expiry = {}; self.payload = {}
    async def eval(self, script, numkeys, *all_args):
        keys, args = all_args[:numkeys], all_args[numkeys:]
        if "HGET" in script:
            geo, expiry, track = keys; version, deadline, now, lon, lat, icao, payload = args
            old = self.payload.get(track)
            if old and int(old["version"]) >= int(version): return 0
            if float(deadline) <= float(now): return -1
            self.geo[icao] = (lon, lat); self.expiry[icao] = float(deadline)
            self.payload[track] = {"version": str(version), "deadline": str(deadline), "payload": payload}; return 1
        geo, expiry = keys; now, keyprefix = args
        expired = [icao for icao, deadline in self.expiry.items() if deadline <= float(now)]
        for icao in expired:
            self.geo.pop(icao, None); self.expiry.pop(icao, None); self.payload.pop(keyprefix + icao, None)
        return expired
    async def hgetall(self, key): return self.payload.get(key, {})


@pytest.mark.asyncio
async def test_live_cache_rejects_duplicate_out_of_order_and_prunes():
    client, now = FakeRedis(), datetime.fromtimestamp(200, UTC)
    cache = RedisLiveCache(client, freshness_seconds=120)
    assert await cache.update(observation(150, lon=70), now=now)
    assert not await cache.update(observation(150, lon=71), now=now)
    assert not await cache.update(observation(149, lon=72), now=now)
    assert (await cache.get("abc123", now=now)).lon == 70
    assert await cache.prune(now=datetime.fromtimestamp(271, UTC)) == ["abc123"]
    assert await cache.get("abc123", now=datetime.fromtimestamp(271, UTC)) is None


class FakeS3:
    class exceptions: ClientError = Exception
    def __init__(self): self.data = {}
    def head_bucket(self, **kwargs):
        if "bucket" not in self.data: raise Exception()
    def create_bucket(self, Bucket): self.data["bucket"] = Bucket
    def put_bucket_lifecycle_configuration(self, **kwargs): self.lifecycle = kwargs
    def put_object(self, Bucket, Key, Body, **kwargs): self.data[Key] = Body if isinstance(Body, bytes) else Body.read()
    def get_object(self, Bucket, Key):
        from io import BytesIO
        return {"Body": BytesIO(self.data[Key])}
    def get_paginator(self, name):
        class Pages:
            def __init__(self, data): self.data=data
            def paginate(self, **kwargs): return [{"Contents": [{"Key": key} for key in self.data if key.startswith("manifests/")]}]
        return Pages(self.data)


def test_archive_pending_manifest_recovers_after_publish_failure(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("backend.app.data.archive.boto3.client", lambda *args, **kwargs: fake)
    archive = MinioSnapshotArchive(endpoint="http://fake", access_key="a", secret_key="s", bucket="bucket")
    archive.initialize()
    manifest = archive.archive(b'{"states":[]}', source_id="test", bbox={}, response_time=None,
        ingested_at=datetime(2026, 1, 1, tzinfo=UTC))
    recovered = []
    assert archive.recover_pending(lambda item, raw: recovered.append((item, raw))) == 1
    assert recovered[0][1] == b'{"states":[]}'
    assert json.loads(fake.data[f"manifests/{manifest.checksum}.json"])["state"] == "published"


@pytest.mark.asyncio
async def test_opensky_oauth_401_rate_limit_5xx_timeout_and_empty_feed():
    calls = {"states": 0, "token": 0}
    async def handler(request):
        if str(request.url).startswith(TOKEN_URL):
            calls["token"] += 1; return httpx.Response(200, json={"access_token": f"t{calls['token']}", "expires_in": 60})
        calls["states"] += 1
        if calls["states"] == 1: return httpx.Response(401)
        if calls["states"] == 2: return httpx.Response(200, json={"time": 1, "states": None}, headers={"X-Rate-Limit-Remaining": "12"})
        return httpx.Response(500)
    settings = AdsBSettings(opensky_client_id="id", opensky_client_secret="secret", max_retries=0,
                            bounding_box=BoundingBox(lamin=20,lomin=70,lamax=30,lomax=80))
    client = OpenSkyClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await client.fetch_states())["states"] == [] and calls["token"] == 2 and client.remaining_credits == 12
    with pytest.raises(httpx.HTTPStatusError): await client.fetch_states()
    await client.close()


@pytest.mark.asyncio
async def test_opensky_honours_rate_limit_and_retries_timeout(monkeypatch):
    delays = []
    async def no_sleep(delay): delays.append(delay)
    monkeypatch.setattr("backend.app.data.ingestors.adsb.asyncio.sleep", no_sleep)
    calls = {"count": 0}
    async def handler(request):
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"X-Rate-Limit-Retry-After-Seconds": "7"})
        if calls["count"] == 2:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"time": 1, "states": []})
    settings = AdsBSettings(max_retries=3, bounding_box=BoundingBox(lamin=20,lomin=70,lamax=30,lomax=80))
    client = OpenSkyClient(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await client.fetch_states())["states"] == []
    assert delays[0] == 7 and len(delays) == 2
    await client.close()


def test_conflict_marks_unknown_altitude_instead_of_predicting():
    point = observation(100, lon=73, lat=26.3)
    assert conflict_candidates([point], lon=73, lat=26.3, altitude_m=None, altitude_reference=None,
        observed_at=point.time)[0].assessment == "unknown"


@pytest.mark.asyncio
async def test_history_consumer_does_not_commit_when_write_fails():
    class Consumer:
        def __init__(self): self.committed = False
        async def getone(self):
            class Message: value = observation().to_event().model_dump_json().encode()
            return Message()
        async def commit(self): self.committed = True
    class Writer:
        async def insert(self, item): raise RuntimeError("database unavailable")
    consumer = Consumer()
    with pytest.raises(RuntimeError, match="database unavailable"):
        await HistoryConsumer(consumer, Writer()).process_one()
    assert not consumer.committed
