"""Read-only Postgres access for doctrine search, and the cloud embedder."""

import os
from typing import Any

import asyncpg
import httpx

from garuda.queries.doctrine import EMBEDDING_DIM
from garuda.storage.postgres import asyncpg_dsn

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
READ_ONLY_DSN = "postgresql://llm_ro:llm_ro_dev@localhost:5433/garuda"


class PostgresDoctrineReader:
    """Runs the fixed doctrine SELECT. The session is read-only."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @classmethod
    def from_env(cls) -> "PostgresDoctrineReader":
        dsn = os.environ.get("DOCTRINE_DATABASE_URL", os.environ.get("DATABASE_URL_RO", READ_ONLY_DSN))
        return cls(dsn)

    async def read(self, sql: str, args: tuple[Any, ...], *, timeout_s: float) -> list[dict[str, Any]]:
        connection = await asyncpg.connect(asyncpg_dsn(self._dsn))
        try:
            timeout_ms = str(max(1, int(timeout_s * 1000)))
            await connection.execute("SET default_transaction_read_only = on")
            await connection.execute("SELECT set_config('statement_timeout', $1, false)", timeout_ms)
            records = await connection.fetch(sql, *args)
            return [dict(record) for record in records]
        finally:
            await connection.close()


class OpenAIEmbedder:
    """text-embedding-3-large (3072). GARUDA_EMBEDDING_MODEL overrides the model name."""

    def __init__(self, api_key: str | None = None, model: str | None = None, timeout_s: float = 10) -> None:
        self._api_key = api_key
        self._model = model or os.environ.get("GARUDA_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
        self._timeout_s = timeout_s

    @classmethod
    def from_env(cls) -> "OpenAIEmbedder":
        return cls(api_key=os.environ.get("OPENAI_API_KEY"))

    async def embed(self, text: str) -> list[float]:
        api_key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
                EMBEDDINGS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self._model, "input": text},
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
        if not isinstance(vector, list) or len(vector) != EMBEDDING_DIM:
            raise RuntimeError(f"embedding model returned {len(vector) if isinstance(vector, list) else 'no'} values")
        return [float(value) for value in vector]
