"""In-memory transcript for one task, plus the query-agent sessions it opened."""

from garuda.harness.retrieval.session import RetrievalSession


class TaskSession(RetrievalSession):
    def __init__(self) -> None:
        super().__init__()
        self.children: dict[str, RetrievalSession] = {}

    def close(self) -> None:
        super().close()
        for child in self.children.values():
            child.close()
        self.children.clear()
