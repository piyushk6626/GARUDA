def shape_for_llm(data: dict, *, max_rows: int = 20) -> dict:
    """Top-N rows, counts, truncation flags, ref handles (<~4k tokens). TODO(phase1)."""
    raise NotImplementedError
