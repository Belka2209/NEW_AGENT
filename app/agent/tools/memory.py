from __future__ import annotations

from app.db import store


def memory_add(text: str) -> str:
    note = (text or "").strip()
    if not note:
        raise ValueError("Пустая заметка")
    item = store.add_memory(note)
    return f"Запомнил #{item['id']}: {item['text']}"


def memory_list() -> str:
    rows = store.list_memories()
    if not rows:
        return "Память пустая."
    return "\n".join(f"#{row['id']}: {row['text']}" for row in rows)


def memory_delete(memory_id: int) -> str:
    if store.delete_memory(int(memory_id)):
        return f"Удалил память #{memory_id}"
    return f"Память #{memory_id} не найдена"
