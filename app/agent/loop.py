from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agent.prompts import build_system_prompt
from app.agent.tool_parse import parse_text_tool_calls, strip_thinking
from app.agent.tools import files
from app.agent.tools.registry import SCHEMAS, execute_tool
from app.config import ROOT, settings
from app.db import store
from app.llm.client import OllamaError, stream_chat
from app.logutil import get_logger


def _dbg(hypothesis_id: str, location: str, message: str, data: dict[str, Any], run_id: str = "post-fix") -> None:
    # #region agent log
    try:
        import time
        from pathlib import Path

        payload = {
            "sessionId": "378790",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with (ROOT / "debug-378790.log").open("a", encoding="utf-8") as handle:
            handle.write(line)
        with (ROOT / "logs" / "agent.log").open("a", encoding="utf-8") as handle:
            handle.write("DEBUG " + line)
        print("DEBUG378790", hypothesis_id, location, message, flush=True)
    except Exception as exc:
        print("DEBUG378790_FAIL", hypothesis_id, location, type(exc).__name__, flush=True)
    # #endregion


_BROWSER_ASK = re.compile(
    r"браузер|вкладк|chrome|"
    r"прочит\w*\s+(страниц|вкладк)|"
    r"что\s+на\s+страниц|"
    r"на\s+страниц|"
    r"что\s+там\s+в\s+браузер|"
    r"что\s+открыто|отображен|сводк",
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
_EDIT_HINT = re.compile(r"исправ|поправ|внеси\s+изменен|замени", re.I)
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


_READ_RANGE = re.compile(
    r"^read_file:\s*(.+?)\s+строки\s+(\d+)-(\d+)\s+из\s+(\d+)",
    re.M,
)


def _read_ok(item: str) -> bool:
    return item.startswith("read_file:") and "строки" in item


def _page_ok(item: str) -> bool:
    return item.startswith("browser_content:") and "chrome-error:" not in item and len(item) > 80


def _has_file_payload(collected: list[str]) -> bool:
    return any(_read_ok(item) for item in collected)


def _has_page_payload(collected: list[str]) -> bool:
    return any(_page_ok(item) for item in collected)


def _last_read_meta(collected: list[str]) -> dict[str, Any] | None:
    for item in reversed(collected):
        match = _READ_RANGE.search(item)
        if match:
            return {
                "display": match.group(1).strip(),
                "start": int(match.group(2)),
                "end": int(match.group(3)),
                "total": int(match.group(4)),
            }
    return None


def _covered_end(collected: list[str], display: str) -> int:
    covered = 0
    for item in collected:
        match = _READ_RANGE.search(item)
        if match and match.group(1).strip() == display:
            covered = max(covered, int(match.group(3)))
    return covered


def _file_fully_read(collected: list[str]) -> bool:
    meta = _last_read_meta(collected)
    if meta is None:
        return False
    return _covered_end(collected, meta["display"]) >= meta["total"]


def _needs_full_file(user_text: str) -> bool:
    if _EDIT_HINT.search(user_text or "") or _WRITE_HINT.search(user_text or ""):
        return False
    return _wants_code(user_text)


def _continue_read(user_text: str, collected: list[str]) -> dict[str, Any] | None:
    if not _needs_full_file(user_text) or _file_fully_read(collected):
        return None
    meta = _last_read_meta(collected)
    if meta is None:
        return None
    covered = _covered_end(collected, meta["display"])
    if covered >= meta["total"]:
        return None
    path = _extract_file_path(user_text) or meta["display"]
    if path.replace("\\", "/").startswith("workspace/"):
        path = path.replace("\\", "/")[len("workspace/") :]
    return _fake_call("read_file", {"path": path, "offset": covered + 1, "limit": 500})


def _should_answer_now(user_text: str, collected: list[str]) -> bool:
    if _has_file_payload(collected) and not _EDIT_HINT.search(user_text or ""):
        if _needs_full_file(user_text) and not _file_fully_read(collected):
            return False
        return True
    if _has_page_payload(collected) and (
        _BROWSER_ASK.search(user_text or "") or _PROMISE_ONLY.search(user_text or "")
    ):
        return True
    if any(item.startswith("edit_file: Изменено:") for item in collected):
        return True
    return False


def _filter_tool_calls(
    user_text: str,
    calls: list[dict[str, Any]],
    collected: list[str],
) -> list[dict[str, Any]]:
    if _should_answer_now(user_text, collected):
        return []
    target = _extract_file_path(user_text)
    if not target or not (
        _FILE_HINT.search(user_text or "") or _wants_code(user_text)
    ):
        return calls
    kept: list[dict[str, Any]] = []
    for call in calls:
        name = _tool_name(call)
        if name == "read_file":
            raw = call.get("function", {}).get("arguments") or "{}"
            try:
                args = json.loads(raw) if str(raw).strip() else {}
            except json.JSONDecodeError:
                args = {}
            path = str(args.get("path") or "")
            if not path or target.lower() in path.lower() or path.lower() in target.lower():
                kept.append(call)
        elif name == "edit_file" and _EDIT_HINT.search(user_text or ""):
            kept.append(call)
    if kept:
        return kept
    if not _has_file_payload(collected):
        return [_fake_call("read_file", {"path": target, "limit": 500})]
    return []


def _force_file_call(user_text: str, collected: list[str]) -> dict[str, Any] | None:
    if _WRITE_HINT.search(user_text or "") or _has_file_payload(collected):
        return None
    wants_file = bool(_FILE_HINT.search(user_text or "") or _wants_code(user_text))
    if not wants_file:
        return None
    path = _extract_file_path(user_text)
    if path:
        return _fake_call("read_file", {"path": path, "limit": 500})
    return None


def _followup_after_tools(code_task: bool, collected: list[str]) -> str:
    last = collected[-1] if collected else ""
    if last.startswith("edit_file:") and ("не найден" in last.lower() or "old_text" in last):
        return (
            "Тот же запрос: фрагмент для правки не найден. "
            "Снова вызови read_file и edit_file с точным текстом. Не здоровайся."
        )
    if code_task:
        if not _file_fully_read(collected):
            return (
                "Файл ещё не дочитан. Не отвечай пользователю и не спрашивай имя файла. "
                "Дочитай оставшиеся строки через read_file с offset."
            )
        return (
            "Файл уже прочитан. Сразу напиши ревью по номерам строк: классы, риски, баги. "
            "Не здоровайся, не спрашивай имя файла и не спрашивай, что делать."
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
    _dbg(
        "B",
        "loop.py:run_turn",
        "turn start",
        {
            "model": model,
            "code_task": code_task,
            "extracted": _extract_file_path(user_text),
            "file_hint": bool(_FILE_HINT.search(user_text or "")),
            "user": user_text[:120],
        },
    )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(code_task=code_task)}
    ]
    messages.extend(history)
    messages.append(user_message)
    collected: list[str] = []

    try:
        for _ in range(settings.max_agent_steps):
            answer_now = _should_answer_now(user_text, collected)
            continued = None if answer_now else _continue_read(user_text, collected)
            content = ""
            tool_calls: list[dict[str, Any]] = []
            after_complete: list[str] = []
            after_filter: list[str] = []
            toolset: list[dict[str, Any]] = []
            if continued:
                tool_calls = [continued]
                after_complete = ["read_file"]
                after_filter = ["read_file"]
            else:
                toolset = [] if answer_now else SCHEMAS
                content, tool_calls = await _complete(messages, toolset, model=model)
                if not content and not tool_calls:
                    content, tool_calls = await _complete(messages, [], model=model)
                after_complete = [_tool_name(call) for call in tool_calls]
                if answer_now:
                    tool_calls = []
                else:
                    tool_calls = _filter_tool_calls(user_text, tool_calls, collected)
                after_filter = [_tool_name(call) for call in tool_calls]
                if not _has_file_tool(tool_calls):
                    forced_file = _force_file_call(user_text, collected)
                    if forced_file:
                        tool_calls = [forced_file]
                if not tool_calls and not _has_browser_tool(tool_calls) and not _has_page_payload(collected):
                    forced = _force_browser_call(user_text, content)
                    if forced and not any(item.startswith("browser_content:") for item in collected):
                        tool_calls = [forced]
            after_force = [_tool_name(call) for call in tool_calls]
            _dbg(
                "A",
                "loop.py:step",
                "tool decision",
                {
                    "answer_now": answer_now,
                    "toolset_empty": not toolset,
                    "content_len": len(content or ""),
                    "after_complete": after_complete,
                    "after_filter": after_filter,
                    "after_force": after_force,
                    "has_file_payload": _has_file_payload(collected),
                    "file_full": _file_fully_read(collected),
                    "continued": bool(continued),
                    "hypothesisD_parsed_tools": bool(after_complete) and not toolset,
                },
            )

            if not tool_calls:
                if (
                    code_task
                    and _has_file_payload(collected)
                    and len((content or "").strip()) < 200
                ):
                    _dbg(
                        "E",
                        "loop.py:short_retry",
                        "retry short answer",
                        {"content_head": (content or "")[:160], "content_len": len(content or "")},
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "Это не ревью. Напиши ревью по уже прочитанному коду: "
                                "классы, риски, баги с номерами строк. "
                                "Не спрашивай что делать и не проси имя файла."
                            ),
                        }
                    )
                    content, _ignored = await _complete(messages, [], model=model)
                before = content or ""
                content = _final_text(content, collected)
                if not content:
                    content = "Модель вернула пустой ответ. Напишите /new и спросите ещё раз."
                _dbg(
                    "E",
                    "loop.py:final",
                    "final answer",
                    {
                        "before_len": len(before),
                        "after_len": len(content or ""),
                        "replaced": before != content,
                        "content_head": (content or "")[:160],
                        "collected": [item.split(":", 1)[0] for item in collected],
                        "file_full": _file_fully_read(collected),
                    },
                )
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
                _dbg(
                    "A",
                    "loop.py:tool_result",
                    "tool finished",
                    {
                        "name": name,
                        "ok_read": _read_ok(f"{name}: {result}"),
                        "answer_now": _should_answer_now(user_text, collected),
                        "file_full": _file_fully_read(collected),
                        "result_head": result[:80],
                    },
                )

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
