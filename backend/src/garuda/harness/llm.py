"""Chat model for harness agents. Tests inject their own model."""

import os

DEFAULT_MODEL = "gpt-6-luna"


def chat_model():
    """GPT-6 Luna. ``GARUDA_LLM_MODEL`` overrides the name (optional ``openai:`` prefix).

    Luna tool-calls on the Chat Completions API only with reasoning effort ``none``.
    """
    raw = os.environ.get("GARUDA_LLM_MODEL", DEFAULT_MODEL)
    name = raw.removeprefix("openai:")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=name, temperature=0, reasoning_effort="none")
