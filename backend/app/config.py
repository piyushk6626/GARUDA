"""Configuration for the ADS-B application; secrets are environment-only."""
from __future__ import annotations

from datetime import timedelta

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BoundingBox(BaseSettings):
    """OpenSky bounding box, deliberately constrained to a non-wrapping extent."""

    lamin: float = 8.0
    lomin: float = 68.0
    lamax: float = 37.0
    lomax: float = 97.0

    @field_validator("lamin", "lamax")
    @classmethod
    def latitude(cls, value: float) -> float:
        if not -90 <= value <= 90:
            raise ValueError("latitude must be in [-90, 90]")
        return value

    @field_validator("lomin", "lomax")
    @classmethod
    def longitude(cls, value: float) -> float:
        if not -180 <= value <= 180:
            raise ValueError("longitude must be in [-180, 180]")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.lamin >= self.lamax or self.lomin >= self.lomax:
            raise ValueError("bounding box minima must be smaller than maxima")

    def query_params(self) -> dict[str, float]:
        return self.model_dump()


class AdsBSettings(BaseSettings):
    """All defaults support local offline work without OpenSky credentials."""

    model_config = SettingsConfigDict(env_file="infra/adsb/.env", env_prefix="ADSB_", extra="ignore")

    source_id: str = "opensky"
    bounding_box: BoundingBox = Field(default_factory=BoundingBox)
    poll_interval_seconds: float = Field(default=15, ge=10, le=3600)
    request_timeout_seconds: float = Field(default=10, gt=0, le=60)
    max_retries: int = Field(default=3, ge=0, le=10)
    live_freshness_seconds: int = Field(default=120, ge=1, le=3600)
    redis_url: str = "redis://:garuda-redis-local-only@localhost:16379/0"
    postgres_dsn: str = "postgresql://garuda:garuda-local-only@127.0.0.1:15432/garuda"
    kafka_bootstrap_servers: str = "localhost:19092"
    minio_endpoint: str = "http://localhost:19000"
    minio_access_key: str = Field(default="garuda-local", validation_alias="ADSB_MINIO_USER")
    minio_secret_key: SecretStr = Field(default=SecretStr("garuda-minio-local-only"), validation_alias="ADSB_MINIO_PASSWORD")
    snapshot_bucket: str = "garuda-adsb-raw"
    opensky_client_id: str | None = None
    opensky_client_secret: SecretStr | None = None

    @property
    def token_enabled(self) -> bool:
        return bool(self.opensky_client_id and self.opensky_client_secret)

    @property
    def freshness(self) -> timedelta:
        return timedelta(seconds=self.live_freshness_seconds)
