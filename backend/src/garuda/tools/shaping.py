"""Shrink tool outputs before they go back into an LLM context."""

from typing import Any


def shape_for_llm(data: dict, *, max_rows: int = 20) -> dict:
    """Keep the top ``max_rows`` of every list. A cut list gets ``<key>_total`` and ``<key>_truncated``."""
    return _shape_dict(data, max_rows)


def _shape_dict(data: dict, max_rows: int) -> dict:
    shaped: dict[str, Any] = {}
    for key, value in data.items():
        shaped[key] = _shape(value, max_rows)
        if isinstance(value, list) and len(value) > max_rows:
            shaped[f"{key}_total"] = len(value)
            shaped[f"{key}_truncated"] = True
    return shaped


def _shape(value: Any, max_rows: int) -> Any:
    if isinstance(value, dict):
        return _shape_dict(value, max_rows)
    if isinstance(value, list):
        return [_shape(item, max_rows) for item in value[:max_rows]]
    return value
