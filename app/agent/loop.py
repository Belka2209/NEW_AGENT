from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agent.prompts import build_system_prompt
from app.agent.tool_parse import parse_text_tool_calls, strip_thinking
from app.agent.tools import files
from app.agent.tools.registry import SCHEMAS, execute_tool
from app.config import settings
from app.db import store
from app.llm.client import OllamaError, stream_chat
from app.logutil import get_logger


_BROWSER_ASK = re.compile(
    r"браузер|вкладк|chrome|"
    r"прочит\w*\s+(страниц|вкладк)|"
    r"что\s+на\s+страниц|"
    r"что\s+там\s+в\s+браузер|"
    r"что\s+открыто",
    re.I,
)
_BROWSER_TABS = re.compile(r"вкладк|что открыто", re.I)
_USELESS_REPLY = re.compile(
    r"нет контекста|начало нашего общения|чем могу помочь|"
    r"что бы вы хотели|я готов помочь|напишите, что нужно|"
    r"узнать погоду|найти вакансии|работать с файлами|"
    r"нет доступа к истории|новый чат без контекста|"
    r"прошлых запросов|уточните.{0,40}задач",
    re.I,
)
_PROMISE_ONLY = re.compile(
    r"сейчас прочитаю|сейчас посмотрю|открою вкладк|сейчас открою",
    re.I,
)
_FILE_HINT = re.compile(
    r"файл|проверь|прочит|открой|покажи|что\s+там\s+за\s+код|путь|адрес",
    re.I,
)
_WRITE_HINT = re.compile(r"создай|запиши|сохрани|перезапиши", re.I)
_PATH_TOKEN = re.compile(
    r'"([^"]+)"|'
    r"'([^']+)'|"
    r"([A-Za-z]:[\\/][^\s]+)|"
    r"((?:[\w.\-]+[\\/])+[\w.\-]+)|"
    r"([\w.\-]+\.[A-Za-z0-9]{1,12})"
)
_FILE_TOOLS = {"read_file", "list_files", "search_files", "write_file", "edit_file"}
_CODE_ASK = re.compile(
    r"\b(ревью|review|рефактор|refactor|линт|lint|баг|bug|pytest|unittest)\b|"
    r"исправ(ь|ить)|поправ(ь|ить)|внеси\s+изменен|"
    r"напиши\s+(код|функц|класс|скрипт)|"
    r"что\s+(делает|за\s+код)|"
    r"\.(py|js|ts|tsx|jsx|go|rs|java|kt|cs|php|rb|cpp|c|h|sql|ps1|sh|vue)\b",
    re.I,
)


def _wants_code(text: str) -> bool:
    return bool(_CODE_ASK.search(text or ""))


def _turn_model(user_text: str) -> str:
    if _wants_code(user_text) and (settings.ollama_code_model or "").strip():
        return settings.ollama_code_model.strip()
    return settings.ollama_model


def _tool_name(call: dict[str, Any]) -> str:
    return (call.get("function") or {}).get("name") or ""


def _fake_call(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": f"call_{name}_auto",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args or {}, ensure_ascii=False),
        },
    }


def _has_browser_tool(calls: list[dict[str, Any]]) -> bool:
    return any(_tool_name(call).startswith("browser_") for call in calls)


def _has_file_tool(calls: list[dict[str, Any]]) -> bool:
    return any(_tool_name(call) in _FILE_TOOLS for call in calls)


def _extract_file_path(text: str) -> str | None:
    found: list[str] = []
    for match in _PATH_TOKEN.finditer(text or ""):
        value = next((item for item in match.groups() if item), "")
        value = value.strip().strip(".,;")
        if value and value.lower() not in {"workspace", "chrome"}:
            found.append(value)
    return found[-1] if found else None


def _force_file_call(user_text: str) -> dict[str, Any] | None:
    if _WRITE_HINT.search(user_text or ""):
        return None
    wants_file = bool(_FILE_HINT.search(user_text or "") or _wants_code(user_text))
    if not wants_file:
        return None
    path = _extract_file_path(user_text)
    if path:
        return _fake_call("read_file", {"path": path, "limit": 500})
    if _wants_code(user_text) and re.search(r"ревью|review|проверь", user_text or "", re.I):
        return _fake_call("list_files", {"path": "."})
    return None


def _followup_after_tools(code_task: bool, collected: list[str]) -> str:
    last = collected[-1] if collected else ""
    if last.startswith("edit_file:") and ("не найден" in last.lower() or "old_text" in last):
        return (
            "Тот же запрос: фрагмент для правки не найден. "
            "Снова вызови read_file и edit_file с точным текстом. Не здоровайся."
        )
    if code_task:
        return (
            "Тот же запрос про код. Ответь по результату инструментов: "
            "ревью по строкам или что изменено. Не здоровайся и не пиши, что нет контекста. "
            "Не вызывай инструменты снова, если данных хватает."
        )
    return (
        "Тот же запрос. Перескажи результат инструментов по делу. "
        "Не здоровайся и не пиши, что нет контекста. "
        "Не вызывай инструменты снова, если данных хватает."
    )


async def _run_verify(path: str) -> str:
    command = (settings.verify_command or "").strip()
    if not command:
        return ""
    try:
        cwd = files.resolve_workdir(path)
    except Exception:
        cwd = "."
    return await execute_tool("run_command", {"command": command, "cwd": cwd})


def _force_browser_call(user_text: str, content: str) -> dict[str, Any] | None:
    text = f"{user_text}\n{content}"
    if not _BROWSER_ASK.search(text) and not _PROMISE_ONLY.search(content or ""):
        return None
    name = "browser_status" if _BROWSER_TABS.search(user_text) else "browser_content"
    return _fake_call(name)


def _final_text(content: str, collected: list[str]) -> str:
    text = (content or "").strip()
    if collected and (not text or _USELESS_REPLY.search(text) or _PROMISE_ONLY.search(text)):
        return "Вот что получилось:\n\n" + "\n\n".join(collected)
    return text


def _title_from(text: str) -> str:
    clean = " ".join(text.strip().split())
    if len(clean) <= 42:
        return clean or "Новый чат"
    return clean[:41] + "…"


async def _complete(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    model: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    content = ""
    tool_calls: list[dict[str, Any]] = []
    async for event in stream_chat(messages, tools, model=model):
        if event["type"] == "token":
            content += event["text"]
        elif event["type"] == "complete":
            content = event["content"]
            tool_calls = event["tool_calls"]
    content = strip_thinking(content)
    if not tool_calls:
        parsed, leftover = parse_text_tool_calls(content)
        return leftover, parsed
    _, leftover = parse_text_tool_calls(content)
    return leftover, tool_calls


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
    log = get_logger()
    model = _turn_model(user_text)
    code_task = _wants_code(user_text)
    log.info("turn session=%s model=%s user=%s", session_id, model, user_text[:300])

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(code_task=code_task)}
    ]
    messages.extend(history)
    messages.append(user_message)
    collected: list[str] = []

    try:
        for _ in range(settings.max_agent_steps):
            content, tool_calls = await _complete(messages, SCHEMAS, model=model)
            if not content and not tool_calls:
                content, tool_calls = await _complete(messages, [], model=model)

            if not _has_file_tool(tool_calls):
                forced_file = _force_file_call(user_text)
                if forced_file and not any(item.startswith("read_file:") for item in collected):
                    tool_calls = [forced_file]
            if not tool_calls and not _has_browser_tool(tool_calls):
                forced = _force_browser_call(user_text, content)
                if forced and not any(
                    item.startswith("browser_") for item in collected
                ):
                    tool_calls = [forced]

            if not tool_calls:
                content = _final_text(content, collected)
                if not content:
                    content = "Модель вернула пустой ответ. Напишите /new и спросите ещё раз."
                store.add_message(session_id, {"role": "assistant", "content": content})
                yield {"type": "token", "text": content}
                yield {"type": "done"}
                return

            assistant: dict[str, Any] = {"role": "assistant", "content": content or ""}
            assistant["tool_calls"] = tool_calls
            store.add_message(session_id, assistant)
            messages.append(assistant)

            for call in tool_calls:
                name = call.get("function", {}).get("name") or "unknown"
                raw_args = call.get("function", {}).get("arguments") or "{}"
                try:
                    parsed_args = json.loads(raw_args) if str(raw_args).strip() else {}
                except json.JSONDecodeError:
                    parsed_args = {"_raw": raw_args}

                yield {"type": "tool_start", "name": name, "args": parsed_args}
                log.info("tool %s args=%s", name, str(parsed_args)[:400])
                result = await execute_tool(name, raw_args)
                log.info("tool_result %s %s", name, result[:400])
                yield {"type": "tool_result", "name": name, "result": result}
                collected.append(f"{name}: {result}")

                tool_message = {
                    "role": "tool",
                    "tool_call_id": call.get("id") or name,
                    "name": name,
                    "content": result,
                }
                store.add_message(session_id, tool_message)
                messages.append(tool_message)

                if (
                    name == "edit_file"
                    and result.startswith("Изменено:")
                    and (settings.verify_command or "").strip()
                ):
                    verify = await _run_verify(str(parsed_args.get("path") or ""))
                    if verify:
                        yield {"type": "tool_start", "name": "run_command", "args": {"command": settings.verify_command}}
                        yield {"type": "tool_result", "name": "run_command", "result": verify}
                        collected.append(f"run_command: {verify}")
                        verify_message = {
                            "role": "tool",
                            "tool_call_id": "call_verify_auto",
                            "name": "run_command",
                            "content": verify,
                        }
                        store.add_message(session_id, verify_message)
                        messages.append(verify_message)

            messages.append(
                {
                    "role": "system",
                    "content": _followup_after_tools(code_task, collected),
                }
            )

        if collected:
            yield {
                "type": "token",
                "text": "Вот что получилось:\n\n" + "\n\n".join(collected),
            }
        yield {
            "type": "error",
            "message": f"Достигнут лимит шагов агента ({settings.max_agent_steps})",
        }
    except OllamaError as exc:
        get_logger().error("llm %s", exc)
        yield {"type": "error", "message": str(exc)}
