from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.agent.tools import clock, files, terminal, web

ToolFn = Callable[..., str]

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов и папок внутри workspace/. path относительный.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Относительный путь, по умолчанию корень workspace"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать текстовый файл из workspace/ с нумерацией строк.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "Первая строка, с 1"},
                    "limit": {"type": "integer", "description": "Сколько строк прочитать"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Создать или перезаписать текстовый файл внутри workspace/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Поиск подстроки в текстовых файлах workspace/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string", "description": "Где искать, по умолчанию весь workspace"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "current_datetime",
            "description": "Текущие дата и время на этой машине. Для вопросов «какое сегодня число».",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Выполнить команду в workspace/ (PowerShell на Windows). Без интерактива.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поиск в интернете через DuckDuckGo. Ключ API не нужен.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
]

_HANDLERS: dict[str, ToolFn] = {
    "list_files": files.list_files,
    "read_file": files.read_file,
    "write_file": files.write_file,
    "search_files": files.search_files,
    "run_command": terminal.run_command,
    "web_search": web.web_search,
    "current_datetime": clock.current_datetime,
}


def _parse_args(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = raw.strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Аргументы инструмента должны быть объектом")
    return parsed


async def execute_tool(name: str, raw_args: str | dict[str, Any] | None) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Неизвестный инструмент: {name}"
    try:
        args = _parse_args(raw_args)
        return await asyncio.to_thread(handler, **args)
    except TypeError as exc:
        return f"Неверные аргументы: {exc}"
    except Exception as exc:
        return f"Ошибка: {exc}"
