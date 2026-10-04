"""Read-only Neo4j access for LLM query tools.

Neo4j Community has no RBAC, so the guarantee is the READ access mode plus a
managed read transaction: the server rejects any write inside it.
"""

import os
from typing import Any

from neo4j import READ_ACCESS, AsyncGraphDatabase, unit_of_work

from garuda.storage.seed import neo4j_settings


class Neo4jGraphReader:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_env(cls) -> "Neo4jGraphReader":
        uri, user, password = neo4j_settings()
        return cls(
            uri,
            os.environ.get("NEO4J_READ_USER", user),
            os.environ.get("NEO4J_READ_PASSWORD", password),
        )

    async def read(self, cypher: str, params: dict[str, Any], *, timeout_s: float) -> list[dict[str, Any]]:
        @unit_of_work(timeout=timeout_s)
        async def fetch(tx) -> list[dict[str, Any]]:
            result = await tx.run(cypher, params)
            return await result.data()

        async with self._driver.session(default_access_mode=READ_ACCESS) as session:
            return await session.execute_read(fetch)

    async def close(self) -> None:
        await self._driver.close()
