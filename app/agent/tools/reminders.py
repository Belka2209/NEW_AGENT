from __future__ import annotations

from datetime import datetime, timedelta

from app.db import store


def _parse_when(raw: str) -> datetime:
    text = (raw or "").strip()
    if not text:
        raise ValueError("Не указано время")
    now = datetime.now()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    lower = text.lower().replace(" в ", " ")
    day = None
    clock = None
    if lower.startswith("сегодня"):
        day = now.date()
        clock = lower.replace("сегодня", "", 1).strip()
    elif lower.startswith("завтра"):
        day = (now + timedelta(days=1)).date()
        clock = lower.replace("завтра", "", 1).strip()
    if day is not None:
        if not clock:
            return datetime.combine(day, datetime.min.time().replace(hour=9))
        for fmt in ("%H:%M", "%H.%M"):
            try:
                parsed = datetime.strptime(clock, fmt).time()
                return datetime.combine(day, parsed)
            except ValueError:
                continue
    raise ValueError(
        "Не понял время. Примеры: 2026-09-09 18:00, сегодня 18:00, завтра 09:30"
    )


def reminder_add(text: str, when: str) -> str:
    note = (text or "").strip()
    if not note:
        raise ValueError("Пустой текст напоминания")
    due = _parse_when(when)
    item = store.add_reminder(note, due.strftime("%Y-%m-%d %H:%M"))
    return f"Напоминание #{item['id']} на {item['due_at']}: {item['text']}"


def reminder_list() -> str:
    rows = store.list_reminders(include_done=False)
    if not rows:
        return "Активных напоминаний нет."
    now = datetime.now()
    lines = []
    for row in rows:
        due = datetime.strptime(row["due_at"], "%Y-%m-%d %H:%M")
        mark = "просрочено" if due <= now else "ждёт"
        lines.append(f"#{row['id']} [{mark}] {row['due_at']} — {row['text']}")
    return "\n".join(lines)


def reminder_done(reminder_id: int) -> str:
    if store.complete_reminder(int(reminder_id)):
        return f"Закрыл напоминание #{reminder_id}"
    return f"Напоминание #{reminder_id} не найдено"


def due_reminder_lines() -> list[str]:
    now = datetime.now()
    lines = []
    for row in store.list_reminders(include_done=False):
        due = datetime.strptime(row["due_at"], "%Y-%m-%d %H:%M")
        if due <= now:
            lines.append(f"#{row['id']} {row['due_at']} — {row['text']}")
    return lines
