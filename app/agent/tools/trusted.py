from __future__ import annotations

from app import trusted


def trust_folder(path: str) -> str:
    folder = trusted.add_folder(path)
    return f"Добавлено в доверенные: {folder}\n\n{trusted.format_list()}"


def untrust_folder(path: str) -> str:
    removed = trusted.remove_folder(path)
    names = ", ".join(str(item) for item in removed)
    return f"Исключено из доверенных: {names}\n\n{trusted.format_list()}"


def trusted_list() -> str:
    return trusted.format_list()
