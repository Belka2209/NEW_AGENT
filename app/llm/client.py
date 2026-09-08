from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings


class OllamaError(RuntimeError):
    pass


async def check_ollama() -> dict[str, Any]:
    url = settings.ollama_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/api/tags")
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "model": settings.ollama_model,
            "models": [],
            "error": str(exc),
        }

    names = [item.get("name", "") for item in payload.get("models", [])]
    return {
        "ok": True,
        "model": settings.ollama_model,
        "models": names,
        "error": None,
    }


def _accumulate_tool_calls(
    bucket: dict[int, dict[str, Any]],
    deltas: list[dict[str, Any]],
) -> None:
    for delta in deltas:
        index = delta.get("index", 0)
        current = bucket.setdefault(
            index,
            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if delta.get("id"):
            current["id"] = delta["id"]
        if delta.get("type"):
            current["type"] = delta["type"]
        fn = delta.get("function") or {}
        if fn.get("name"):
            current["function"]["name"] += fn["name"]
        if fn.get("arguments"):
            current["function"]["arguments"] += fn["arguments"]


async def stream_chat(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    url = f"{settings.ollama_base_url.rstrip('/')}/v1/chat/completions"
    body = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": True,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.3,
    }

    content = ""
    tool_bucket: dict[int, dict[str, Any]] = {}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            async with client.stream("POST", url, json=body) as response:
                if response.status_code >= 400:
                    error_text = (await response.aread()).decode("utf-8", errors="replace")
                    raise OllamaError(f"Ollama {response.status_code}: {error_text[:500]}")

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    chunk = json.loads(data)
                    choice = (chunk.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        content += delta["content"]
                        yield {"type": "token", "text": delta["content"]}
                    if delta.get("tool_calls"):
                        _accumulate_tool_calls(tool_bucket, delta["tool_calls"])
    except httpx.HTTPError as exc:
        raise OllamaError(f"Не удалось обратиться к Ollama: {exc}") from exc

    tool_calls = [tool_bucket[i] for i in sorted(tool_bucket)]
    for call in tool_calls:
        if not call.get("id"):
            call["id"] = f"call_{call['function']['name']}_{len(call['function']['arguments'])}"

    yield {
        "type": "complete",
        "content": content,
        "tool_calls": tool_calls,
    }
