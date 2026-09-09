from __future__ import annotations

import os
import threading
from pathlib import Path

from app.config import ROOT, settings

ENV_KEY = "TRUSTED_FOLDERS"
_SEPARATOR = ";"
_lock = threading.Lock()


class TrustedError(ValueError):
    pass


def _workspace() -> Path:
    return settings.workspace_dir.resolve()


def _parse(raw: str) -> list[Path]:
    items: list[Path] = []
    seen: set[str] = set()
    for part in (raw or "").split(_SEPARATOR):
        text = part.strip().strip('"').strip("'")
        if not text:
            continue
        path = Path(os.path.expandvars(os.path.expanduser(text))).resolve()
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        items.append(path)
    return items


def extra_folders() -> list[Path]:
    return _parse(settings.trusted_folders)


def all_roots() -> list[Path]:
    roots = [_workspace()]
    seen = {str(_workspace()).casefold()}
    for path in extra_folders():
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def is_inside_trusted(path: Path) -> bool:
    resolved = path.resolve()
    for root in all_roots():
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def display_path(path: Path) -> str:
    workspace = _workspace()
    resolved = path.resolve()
    if resolved == workspace:
        return "workspace/"
    try:
        return "workspace/" + resolved.relative_to(workspace).as_posix()
    except ValueError:
        return str(resolved)


def _is_drive_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved.parent == resolved


def _normalize_incoming(raw: str) -> Path:
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise TrustedError("Пустой путь")
    if _SEPARATOR in text:
        raise TrustedError("В пути не должно быть точки с запятой")
    path = Path(os.path.expandvars(os.path.expanduser(text)))
    if not path.is_absolute() and not (len(text) >= 2 and text[1] == ":"):
        raise TrustedError("Нужен полный путь к папке, например D:\\Docs")
    return path


def _persist(paths: list[Path]) -> None:
    value = _SEPARATOR.join(p.resolve().as_posix() for p in paths)
    settings.trusted_folders = value
    env_path = ROOT / ".env"
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    found = False
    prefix = f"{ENV_KEY}="
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            found = True
        else:
            out.append(line)
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append(prefix + value)
    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def add_folder(raw: str) -> Path:
    incoming = _normalize_incoming(raw)
    if incoming.exists() and incoming.is_file():
        incoming = incoming.parent
    if not incoming.exists() or not incoming.is_dir():
        raise TrustedError(f"Папка не найдена: {incoming}")
    resolved = incoming.resolve()
    if _is_drive_root(resolved):
        raise TrustedError("Корень диска в доверенные нельзя")
    if resolved == _workspace():
        return resolved
    with _lock:
        current = extra_folders()
        key = str(resolved).casefold()
        if any(str(item).casefold() == key for item in current):
            return resolved
        current.append(resolved)
        _persist(current)
    return resolved


def remove_folder(raw: str) -> list[Path]:
    query = (raw or "").strip().strip('"').strip("'")
    if not query:
        raise TrustedError("Пустой путь или имя папки")
    workspace = _workspace()
    try:
        as_path = Path(os.path.expandvars(os.path.expanduser(query)))
        resolved_query = as_path.resolve() if as_path.exists() else None
    except OSError:
        resolved_query = None

    with _lock:
        kept: list[Path] = []
        removed: list[Path] = []
        for item in extra_folders():
            name_hit = item.name.casefold() == query.casefold()
            path_hit = str(item).casefold() == query.casefold()
            resolved_hit = resolved_query is not None and item == resolved_query
            posix_hit = item.as_posix().casefold() == query.replace("\\", "/").casefold()
            if name_hit or path_hit or resolved_hit or posix_hit:
                if item == workspace:
                    continue
                removed.append(item)
            else:
                kept.append(item)
        if not removed:
            raise TrustedError(f"В доверенных нет: {query}")
        _persist(kept)
    return removed


def format_list() -> str:
    lines = [f"- {display_path(_workspace())} (всегда, нельзя исключить)"]
    extras = extra_folders()
    if extras:
        for path in extras:
            mark = "" if path.is_dir() else " — папка сейчас недоступна"
            lines.append(f"- {path}{mark}")
    else:
        lines.append("Других доверенных папок нет.")
    return "\n".join(lines)
