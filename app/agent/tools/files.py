from __future__ import annotations

from pathlib import Path

from app.config import settings

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}
MAX_READ_CHARS = 80_000
MAX_LIST_ITEMS = 200
MAX_SEARCH_HITS = 40


class WorkspaceError(ValueError):
    pass


def safe_path(rel: str | None) -> Path:
    workspace = settings.workspace_dir.resolve()
    raw = (rel or ".").replace("\\", "/").strip()
    if raw.startswith("/") or raw.startswith("~") or (len(raw) >= 2 and raw[1] == ":"):
        raise WorkspaceError("Путь должен быть относительным внутри workspace/")
    candidate = (workspace / raw).resolve()
    try:
        candidate.relative_to(workspace)
    except ValueError as exc:
        raise WorkspaceError("Путь выходит за пределы workspace/") from exc
    return candidate


def _rel(path: Path) -> str:
    workspace = settings.workspace_dir.resolve()
    if path == workspace:
        return "."
    return path.relative_to(workspace).as_posix()


def list_files(path: str = ".") -> str:
    root = safe_path(path)
    if not root.exists():
        raise WorkspaceError(f"Нет такого пути: {path}")
    if root.is_file():
        return _rel(root)

    items: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name in SKIP_DIRS:
            continue
        suffix = "/" if child.is_dir() else ""
        items.append(f"{_rel(child)}{suffix}")
        if len(items) >= MAX_LIST_ITEMS:
            items.append("… список обрезан")
            break
    if not items:
        return "(пусто)"
    return "\n".join(items)


def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    target = safe_path(path)
    if not target.is_file():
        raise WorkspaceError(f"Файл не найден: {path}")
    raw = target.read_bytes()
    if b"\x00" in raw[:4096]:
        raise WorkspaceError("Бинарный файл читать нельзя")
    text = raw.decode("utf-8", errors="replace")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + "\n… файл обрезан"
    lines = text.splitlines()
    start = max(offset, 1)
    end = start + max(limit, 1) - 1
    chunk = lines[start - 1 : end]
    numbered = [f"{start + i:>4}| {line}" for i, line in enumerate(chunk)]
    header = f"{_rel(target)} строки {start}-{min(end, len(lines))} из {len(lines)}"
    return header + "\n" + "\n".join(numbered)


def write_file(path: str, content: str) -> str:
    target = safe_path(path)
    workspace = settings.workspace_dir.resolve()
    if target == workspace:
        raise WorkspaceError("Нельзя перезаписать корень workspace/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"Записано: {_rel(target)} ({len(content)} символов)"


def search_files(query: str, path: str = ".") -> str:
    root = safe_path(path)
    if not query.strip():
        raise WorkspaceError("Пустой запрос")
    needle = query.lower()
    hits: list[str] = []

    def walk(current: Path) -> None:
        if len(hits) >= MAX_SEARCH_HITS:
            return
        if current.is_dir():
            if current.name in SKIP_DIRS:
                return
            for child in sorted(current.iterdir()):
                walk(child)
                if len(hits) >= MAX_SEARCH_HITS:
                    return
            return
        if not current.is_file() or current.stat().st_size > 1_000_000:
            return
        try:
            text = current.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        for idx, line in enumerate(text.splitlines(), start=1):
            if needle in line.lower():
                hits.append(f"{_rel(current)}:{idx}: {line.strip()[:200]}")
                if len(hits) >= MAX_SEARCH_HITS:
                    return

    walk(root)
    if not hits:
        return "Совпадений нет"
    return "\n".join(hits)
