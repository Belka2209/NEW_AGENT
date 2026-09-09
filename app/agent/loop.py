from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agent.prompts import build_system_prompt
from app.agent.tool_parse import parse_text_tool_calls, strip_thinking
from app.agent.tools import files
from app.agent.tools.registry import execute_tool, schemas_for
from app.config import settings
from app.db import store
from app.llm.client import OllamaError, stream_chat
from app.logutil import get_logger

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
_STALL_REPLY = re.compile(
    r"буду (дочитыв|ожидать)|ожидать завершения|"
    r"пока (он|файл) не будет полностью|"
    r"после этого сразу напишу|"
    r"напишу ревью по номерам строк",
    re.I,
)
_FILE_HINT = re.compile(
    r"файл|проверь|прочит|открой|покажи|ревью|review|что\s+там\s+за\s+код|путь|адрес",
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
_REVIEW_ASK = re.compile(
    r"\b(ревью|review)\b|что\s+(делает|за\s+код)",
    re.I,
)
_CODE_CONTINUE = re.compile(
    r"улучш|исправ|поправ|на основании|ревью|ещё|еще|ну и|"
    r"а что|почему|как лучше|можно ли|дальше|продолж",
    re.I,
)
_CLARIFY_REPLY = re.compile(
    r"какой файл|уточни|что именно|не указали имя|укажите.*имя файла|общий вопрос",
    re.I,
)
_READ_RANGE = re.compile(
    r"^read_file:\s*(.+?)\s+строки\s+(\d+)-(\d+)\s+из\s+(\d+)",
    re.M,
)


def _wants_code(text: str) -> bool:
    return bool(_CODE_ASK.search(text or ""))


def _is_review(text: str) -> bool:
    return bool(_REVIEW_ASK.search(text or ""))


def _file_chunks_from_history(history: list[dict[str, Any]]) -> list[str]:
    chunks: list[str] = []
    for msg in history:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content") or ""
        name = msg.get("name") or ""
        if name == "read_file" or (
            " строки " in content[:120] and " из " in content[:120]
        ):
            chunks.append(content)
    return chunks


def _is_code_turn(user_text: str, history: list[dict[str, Any]], last_file: str) -> bool:
    if _wants_code(user_text):
        return True
    if last_file and _CODE_CONTINUE.search(user_text or ""):
        return True
    return bool(_file_chunks_from_history(history) and _CODE_CONTINUE.search(user_text or ""))


def _turn_model(code_task: bool) -> str:
    if code_task and (settings.ollama_code_model or "").strip():
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


def _target_path(user_text: str, fallback: str = "") -> str:
    path = _extract_file_path(user_text) or (fallback or "")
    if path.replace("\\", "/").startswith("workspace/"):
        path = path.replace("\\", "/")[len("workspace/") :]
    return path


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
    return _is_review(user_text) or _wants_code(user_text)


def _continue_read(user_text: str, collected: list[str], fallback: str = "") -> dict[str, Any] | None:
    if not _needs_full_file(user_text) or _file_fully_read(collected):
        return None
    meta = _last_read_meta(collected)
    if meta is None:
        return None
    covered = _covered_end(collected, meta["display"])
    if covered >= meta["total"]:
        return None
    path = _target_path(user_text, fallback or meta["display"])
    if not path:
        return None
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
    fallback: str = "",
) -> list[dict[str, Any]]:
    if _should_answer_now(user_text, collected):
        return []
    target = _target_path(user_text, fallback)
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


def _force_file_call(
    user_text: str,
    collected: list[str],
    fallback: str = "",
) -> dict[str, Any] | None:
    if _WRITE_HINT.search(user_text or "") or _has_file_payload(collected):
        return None
    wants_file = bool(_FILE_HINT.search(user_text or "") or _wants_code(user_text))
    if not wants_file:
        return None
    path = _target_path(user_text, fallback)
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
        return (
            "Файл уже в результатах инструментов. Сразу напиши готовый ответ по коду. "
            "Не пиши, что будешь читать или ждать. Не спрашивай имя файла."
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


def _thin_code_answer(text: str, code_task: bool) -> bool:
    if not code_task:
        return False
    clean = (text or "").strip()
    return (
        len(clean) < 200
        or bool(_STALL_REPLY.search(clean))
        or bool(_CLARIFY_REPLY.search(clean))
    )


async def _force_review(user_text: str, collected: list[str], model: str) -> str:
    chunks: list[str] = []
    for item in collected:
        if item.startswith("read_file:"):
            chunks.append(item.split(":", 1)[1].lstrip())
        else:
            chunks.append(item)
    messages = [
        {
            "role": "system",
            "content": (
                "Ты разработчик. Ниже уже полный текст файла. "
                "Ответь на запрос пользователя по этому коду. "
                "Если просят ревью — что за файл, затем замечания с номерами строк. "
                "Запрещено писать, что будешь читать, дочитывать или ждать."
            ),
        },
        {"role": "user", "content": f"{user_text}\n\n" + "\n\n".join(chunks)},
    ]
    content, _ignored = await _complete(messages, [], model=model)
    return content


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


def _remember_file(session_id: str, user_text: str, collected: list[str], fallback: str) -> str:
    path = _target_path(user_text, fallback)
    if not path:
        meta = _last_read_meta(collected)
        if meta:
            path = _target_path("", meta["display"])
    if path:
        store.set_session_state(session_id, last_file=path)
    return path


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


async def run_turn(
    session_id: str,
    user_text: str,
    mode: str = "auto",
) -> AsyncIterator[dict[str, Any]]:
    session = store.get_session(session_id)
    if session is None:
        yield {"type": "error", "message": "Диалог не найден"}
        return

    history = store.llm_history(session_id)
    if not history:
        store.rename_session(session_id, _title_from(user_text))
        yield {"type": "title", "title": _title_from(user_text)}

    state = store.get_session_state(session_id)
    last_file = state.get("last_file") or ""
    last_review = state.get("last_review") or ""
    mode = (mode or "auto").strip().lower()
    if mode == "code":
        code_task = True
    elif mode == "chat":
        code_task = False
    else:
        code_task = _is_code_turn(user_text, history, last_file)
        mode = "code" if code_task else "chat"

    user_message = {"role": "user", "content": user_text}
    store.add_message(session_id, user_message)
    log = get_logger()
    model = _turn_model(code_task)
    target = _target_path(user_text, last_file)
    log.info("turn session=%s mode=%s model=%s user=%s", session_id, mode, model, user_text[:300])
    yield {"type": "meta", "mode": mode, "model": model}

    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": build_system_prompt(
                code_task=code_task,
                last_file=last_file,
                last_review=last_review,
            ),
        }
    ]
    messages.extend(history)
    messages.append(user_message)
    collected: list[str] = []
    toolset = schemas_for("code" if code_task else "chat")

    try:
        for _ in range(settings.max_agent_steps):
            answer_now = _should_answer_now(user_text, collected)
            continued = None if answer_now else _continue_read(user_text, collected, target)
            content = ""
            tool_calls: list[dict[str, Any]] = []
            if continued:
                tool_calls = [continued]
            elif (
                code_task
                and not collected
                and target
                and (_is_review(user_text) or _needs_full_file(user_text))
                and not _EDIT_HINT.search(user_text or "")
                and not _WRITE_HINT.search(user_text or "")
            ):
                tool_calls = [_fake_call("read_file", {"path": target, "limit": 500})]
            elif answer_now and code_task and _file_fully_read(collected):
                content = ""
            elif (
                code_task
                and not collected
                and last_review
                and _CODE_CONTINUE.search(user_text or "")
                and not _is_review(user_text)
                and not _EDIT_HINT.search(user_text or "")
            ):
                content, tool_calls = await _complete(messages, [], model=model)
                tool_calls = []
            else:
                content, tool_calls = await _complete(messages, [] if answer_now else toolset, model=model)
                if not content and not tool_calls:
                    content, tool_calls = await _complete(messages, [], model=model)
                if answer_now:
                    tool_calls = []
                else:
                    tool_calls = _filter_tool_calls(user_text, tool_calls, collected, target)
                if not _has_file_tool(tool_calls):
                    forced_file = _force_file_call(user_text, collected, target)
                    if forced_file:
                        tool_calls = [forced_file]
                if not tool_calls and not _has_browser_tool(tool_calls) and not _has_page_payload(collected):
                    forced = _force_browser_call(user_text, content)
                    if forced and not any(item.startswith("browser_content:") for item in collected):
                        tool_calls = [forced]

            if not tool_calls:
                source = collected if _has_file_payload(collected) else [
                    f"read_file: {chunk}" for chunk in _file_chunks_from_history(history)
                ]
                if _thin_code_answer(content, code_task) and source:
                    content = await _force_review(user_text, source, model)
                elif _thin_code_answer(content, code_task) and last_review:
                    content = last_review
                content = _final_text(content, collected)
                if not content:
                    content = "Модель вернула пустой ответ. Напишите /new и спросите ещё раз."
                remembered = _remember_file(session_id, user_text, collected, last_file)
                if code_task and len(content) >= 200:
                    store.set_session_state(
                        session_id,
                        last_file=remembered or last_file,
                        last_review=content,
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

            if not (_needs_full_file(user_text) and not _file_fully_read(collected)):
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
