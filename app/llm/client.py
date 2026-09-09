from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import settings


class OllamaError(RuntimeError):
    pass


def _llm_base() -> str:
    return settings.ollama_base_url.rstrip("/").removesuffix("/v1")


async def check_ollama() -> dict[str, Any]:
    url = _llm_base()
    names: list[str] = []
    last_error = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                response = await client.get(f"{url}/api/tags")
                response.raise_for_status()
                names = [item.get("name", "") for item in response.json().get("models", [])]
            except httpx.HTTPError as exc:
                last_error = str(exc)
                response = await client.get(f"{url}/v1/models")
                response.raise_for_status()
                names = [item.get("id", "") for item in response.json().get("data", [])]
                last_error = None
    except httpx.HTTPError as exc:
        last_error = str(exc)

    if last_error:
        return {
            "ok": False,
            "model": settings.ollama_model,
            "models": [],
            "error": last_error,
        }
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
    url = f"{_llm_base()}/v1/chat/completions"
    body = {
        "model": settings.ollama_model,
        "messages": messages,
        "stream": True,
        "tools": tools,
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_tokens": 4096,
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
