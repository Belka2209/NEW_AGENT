from __future__ import annotations

from datetime import datetime

_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def current_datetime() -> str:
    now = datetime.now()
    return (
        f"{now.day} {_MONTHS[now.month - 1]} {now.year}, "
        f"{_WEEKDAYS[now.weekday()]}, {now:%H:%M}"
    )
