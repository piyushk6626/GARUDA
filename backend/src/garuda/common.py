"""Small helpers shared by generators, ingest, and storage."""

from datetime import datetime, timezone

import numpy as np


def as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def clip(value: float, lower: float, upper: float) -> float:
    return float(min(max(value, lower), upper))


def stream_rng(seed: int, stream: int) -> np.random.Generator:
    """Independent RNG per domain so one domain's draws never shift another's."""
    return np.random.default_rng(np.random.SeedSequence([seed, stream]))
