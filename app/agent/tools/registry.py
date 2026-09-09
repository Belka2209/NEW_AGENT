from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from app.agent.tools import (
    browser,
    clock,
    files,
    hh,
    memory,
    reminders,
    telegram,
    terminal,
    trusted,
    web,
)

ToolFn = Callable[..., str]

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов. path: относительный в workspace/ или полный путь внутри доверенной папки.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Относительный путь в workspace или полный путь в доверенной папке",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Прочитать текстовый файл из workspace/ или доверенной папки.",
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
            "description": "Создать или перезаписать текстовый файл в workspace/ или доверенной папке.",
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
            "description": "Поиск подстроки в текстовых файлах workspace/ или доверенной папки.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Где искать: workspace или полный путь доверенной папки",
                    },
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
            "description": "Выполнить команду (PowerShell на Windows). cwd — workspace или доверенная папка.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {
                        "type": "string",
                        "description": "Рабочая папка: пусто = workspace, иначе полный путь из доверенных",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trust_folder",
            "description": (
                "Добавить папку в TRUSTED_FOLDERS (.env). "
                "Вызывай, когда пользователь дал полный путь и просит там работать."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Полный путь к папке"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "untrust_folder",
            "description": "Убрать папку из доверенных по полному пути или имени (например Downloads).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Полный путь или имя папки"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trusted_list",
            "description": "Показать доверенные папки.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Поиск в интернете: заголовки и выдержки со страниц. Для погоды не использовать.",
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
    {
        "type": "function",
        "function": {
            "name": "hh_search",
            "description": "Поиск вакансий на hh.ru. city: Санкт-Петербург или Москва.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "city": {"type": "string"},
                    "per_page": {"type": "integer"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Фактическая погода сейчас и на сегодня. Передай город, например «Санкт-Петербург».",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_add",
            "description": "Сохранить факт в долгую память (между чатами).",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_list",
            "description": "Показать долгую память.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_delete",
            "description": "Удалить запись памяти по id.",
            "parameters": {
                "type": "object",
                "properties": {"memory_id": {"type": "integer"}},
                "required": ["memory_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_add",
            "description": "Локальное напоминание. when: 2026-09-09 18:00, сегодня 18:00, завтра 09:30.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "when": {"type": "string"},
                },
                "required": ["text", "when"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_list",
            "description": "Список активных локальных напоминаний.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reminder_done",
            "description": "Отметить напоминание выполненным.",
            "parameters": {
                "type": "object",
                "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_status",
            "description": "Проверить, подключён ли Telegram-бот.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_chats",
            "description": "Список алиасов и недавних чатов бота (id, название).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_send",
            "description": "Отправить сообщение в Telegram. chat — алиас из .env, @username или числовой id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["chat", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_start",
            "description": (
                "Сам открыть Chrome агента и показать вкладки. "
                "Всегда используй это вместо скриптов и run_command."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_status",
            "description": "Подключиться к Chrome агента и показать вкладки. Если Chrome нет — запустит сам.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Искать запрос в Chrome (Яндекс) и вернуть текст выдачи. Если web_search пуст — вызови это.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_goto",
            "description": "Открыть любой URL в текущей вкладке Chrome.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_content",
            "description": "Прочитать видимый текст текущей вкладки Chrome.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Клик по тексту на странице (кнопка, ссылка, пункт меню).",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Ввести текст. field — подпись поля, если нужно сначала кликнуть по нему.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "field": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_press",
            "description": "Нажать клавишу, по умолчанию Enter.",
            "parameters": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Скачать текст страницы по http/https ссылке.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                },
                "required": ["url"],
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
    "trust_folder": trusted.trust_folder,
    "untrust_folder": trusted.untrust_folder,
    "trusted_list": trusted.trusted_list,
    "web_search": web.web_search,
    "current_datetime": clock.current_datetime,
    "get_weather": web.get_weather,
    "fetch_url": web.fetch_url,
    "telegram_status": telegram.telegram_status,
    "telegram_chats": telegram.telegram_chats,
    "telegram_send": telegram.telegram_send,
    "memory_add": memory.memory_add,
    "memory_list": memory.memory_list,
    "memory_delete": memory.memory_delete,
    "reminder_add": reminders.reminder_add,
    "reminder_list": reminders.reminder_list,
    "reminder_done": reminders.reminder_done,
    "hh_search": hh.hh_search,
    "browser_status": browser.browser_status,
    "browser_tabs": browser.browser_tabs,
    "browser_goto": browser.browser_goto,
    "browser_content": browser.browser_content,
    "browser_click": browser.browser_click,
    "browser_type": browser.browser_type,
    "browser_press": browser.browser_press,
    "browser_search": browser.browser_search,
    "browser_start": browser.browser_start,
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
