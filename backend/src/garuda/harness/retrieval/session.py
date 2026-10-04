"""In-memory transcript for a single retrieval session.

The graph has no checkpointer. Messages live on this object only while the
parent still owes an answer. close() drops them, so a later call starts empty.
"""

from langchain_core.messages import AnyMessage


class RetrievalSession:
    def __init__(self) -> None:
        self.messages: list[AnyMessage] = []
        self.open = True

    def take_prior(self) -> list[AnyMessage]:
        """Messages still in this session. A closed session contributes nothing."""
        if not self.open:
            self.messages.clear()
            self.open = True
            return []
        return list(self.messages)

    def keep(self, messages: list[AnyMessage]) -> None:
        self.messages = list(messages)
        self.open = True

    def close(self) -> None:
        self.messages.clear()
        self.open = False
