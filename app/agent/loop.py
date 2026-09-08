from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools.registry import SCHEMAS, execute_tool
from app.config import settings
from app.db import store
from app.llm.client import OllamaError, stream_chat


def _title_from(text: str) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= 42:
        return clean or "Новый чат"
    return clean[:41] + "…"


async def run_turn(session_id: str, user_text: str) -> AsyncIterator[dict[str, Any]]:
    session = store.get_session(session_id)
    if session is None:
        yield {"type": "error", "message": "Диалог не найден"}
        return

    history = store.llm_history(session_id)
    if not history:
        store.rename_session(session_id, _title_from(user_text))
        yield {"type": "title", "title": _title_from(user_text)}

    user_message = {"role": "user", "content": user_text}
    store.add_message(session_id, user_message)

    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append(user_message)

    try:
        for _ in range(settings.max_agent_steps):
            content = ""
            tool_calls: list[dict[str, Any]] = []

            async for event in stream_chat(messages, SCHEMAS):
                if event["type"] == "token":
                    content += event["text"]
                    yield event
                elif event["type"] == "complete":
                    content = event["content"]
                    tool_calls = event["tool_calls"]

            assistant: dict[str, Any] = {"role": "assistant", "content": content or ""}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            store.add_message(session_id, assistant)
            messages.append(assistant)

            if not tool_calls:
                yield {"type": "done"}
                return

            for call in tool_calls:
                name = call.get("function", {}).get("name") or "unknown"
                raw_args = call.get("function", {}).get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    parsed_args = {"_raw": raw_args}

                yield {"type": "tool_start", "name": name, "args": parsed_args}
                result = await execute_tool(name, raw_args)
                yield {"type": "tool_result", "name": name, "result": result}

                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": result,
                }
                store.add_message(session_id, tool_message)
                messages.append(tool_message)

        yield {
            "type": "error",
            "message": f"Достигнут лимит шагов агента ({settings.max_agent_steps})",
        }
    except OllamaError as exc:
        yield {"type": "error", "message": str(exc)}
