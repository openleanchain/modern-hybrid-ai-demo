"""Real-model path for the LLM service (used when LLM_MODE=openai).

Kept deliberately thin and in one file so model quirks are easy to adjust for
your exact account. Temperature and max-token caps are intentionally left at the
API defaults to avoid parameter-compatibility errors across model families.
"""
from __future__ import annotations

import os
from typing import Any

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    return _client


# Tasks that must return strict JSON get response_format=json_object.
_JSON_TASKS = {"classify_intent", "extract_entities", "agent_step"}


def complete(model: str, task: str, system: str, prompt: str) -> dict[str, Any]:
    client = _get_client()
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system or "You are a precise enterprise assistant."},
            {"role": "user", "content": prompt},
        ],
    }
    if task in _JSON_TASKS:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    text = resp.choices[0].message.content or ""
    usage = getattr(resp, "usage", None)
    return {
        "text": text,
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        } if usage else {},
    }


def embed(model: str, texts: list[str], dim: int) -> list[list[float]]:
    client = _get_client()
    resp = client.embeddings.create(model=model, input=texts, dimensions=dim)
    return [d.embedding for d in resp.data]
