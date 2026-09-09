from __future__ import annotations

import asyncio
import sys

from app.config import settings
from app.db import store
from app.agent.loop import run_turn
from app.llm.client import check_ollama


def _print(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


async def _one_turn(session_id: str, text: str, mode: str = "auto") -> None:
    thinking = False
    answered = False
    _print("Агент: ")
    async for event in run_turn(session_id, text, mode=mode):
        kind = event.get("type")
        if kind == "token":
            if thinking:
                _print("\nАгент: ")
                thinking = False
            _print(event.get("text") or "")
            answered = True
        elif kind in {"tool_start", "tool_result"}:
            if not answered and not thinking:
                _print("секунду, я думаю…")
                thinking = True
        elif kind == "error":
            _print(f"\nОшибка: {event.get('message')}")
            answered = True
    if not answered:
        _print("пустой ответ. Напишите /new и повторите вопрос.")
    _print("\n")


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdin.reconfigure(encoding="utf-8")

    store.init_db()
    health = await check_ollama()
    session = store.create_session("Терминал")

    print(f"Local Agent · терминал")
    print(f"Модель: {settings.ollama_model}")
    print(f"Сервер: {settings.ollama_base_url}")
    if health.get("ok"):
        print("LLM: подключена")
    else:
        print(f"LLM: нет связи ({health.get('error')})")
        print("Запустите Ollama или LM Studio Local Server.")
    print("Команды: /new /chat /code /auto /exit. Пустая строка — пропуск.\n")

    session_id = session["id"]
    mode = "auto"
    while True:
        try:
            user = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nПока.")
            return
        if not user:
            continue
        if user in {"/exit", "/quit", "/q"}:
            print("Пока.")
            return
        if user == "/new":
            session = store.create_session("Терминал")
            session_id = session["id"]
            print("Новый чат.\n")
            continue
        if user == "/help":
            print("/new новый диалог\n/chat /code /auto режим\n/exit выход\n")
            continue
        if user in {"/chat", "/code", "/auto"}:
            mode = user[1:]
            print(f"Режим: {mode}\n")
            continue
        await _one_turn(session_id, user, mode=mode)


if __name__ == "__main__":
    asyncio.run(main())
