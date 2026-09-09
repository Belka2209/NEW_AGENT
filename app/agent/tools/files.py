from __future__ import annotations

import os
from pathlib import Path

from app import trusted
from app.config import settings

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea"}
MAX_READ_CHARS = 80_000
MAX_LIST_ITEMS = 200
MAX_SEARCH_HITS = 40


class WorkspaceError(ValueError):
    pass


def _is_absolute(raw: str) -> bool:
    if Path(raw).is_absolute():
        return True
    return len(raw) >= 2 and raw[1] == ":"


def _workspace_candidates(raw: str) -> list[str]:
    text = raw.replace("\\", "/").strip()
    if not text or text == ".":
        return ["."]
    variants = [text]
    current = text
    while current.lower() == "workspace" or current.lower().startswith("workspace/"):
        current = "" if current.lower() == "workspace" else current[10:]
        current = current or "."
        if current not in variants:
            variants.append(current)
        if current == ".":
            break
    return variants


def _find_all_by_name(name: str) -> list[Path]:
    needle = name.replace("\\", "/").rstrip("/").split("/")[-1]
    if not needle or needle in {".", ".."}:
        return []
    found: list[Path] = []
    seen: set[str] = set()
    for root in trusted.all_roots():
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [item for item in dirnames if item not in SKIP_DIRS]
            for filename in filenames:
                if filename.casefold() != needle.casefold():
                    continue
                item = Path(dirpath, filename)
                if not trusted.is_inside_trusted(item):
                    continue
                key = str(item.resolve()).casefold()
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
                if len(found) >= 20:
                    return found
    return found


def _existing_under_roots(relative: str) -> Path | None:
    for root in trusted.all_roots():
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.exists():
            return candidate
    return None


def safe_path(rel: str | None) -> Path:
    raw = (rel or ".").strip().strip('"').strip("'")
    raw = os.path.expandvars(os.path.expanduser(raw))
    if not raw:
        raw = "."
    if _is_absolute(raw):
        candidate = Path(raw).resolve()
        if not trusted.is_inside_trusted(candidate):
            raise WorkspaceError(
                f"Путь вне доверенных папок: {candidate}. "
                "Сначала trust_folder с этой папкой."
            )
        return candidate

    if raw.startswith("/") or raw.startswith("~"):
        raise WorkspaceError("Относительный путь — только внутри workspace/ или доверенной папки")

    last: Path | None = None
    workspace = settings.workspace_dir.resolve()
    for variant in _workspace_candidates(raw):
        found = _existing_under_roots(variant)
        if found:
            return found
        candidate = (workspace / variant).resolve()
        try:
            candidate.relative_to(workspace)
            last = candidate
        except ValueError:
            continue

    return last or workspace


def _rel(path: Path) -> str:
    return trusted.display_path(path)


def list_files(path: str = ".") -> str:
    root = safe_path(path)
    if not root.exists():
        raise WorkspaceError(f"Нет такого пути: {path}")
    if root.is_file():
        return _rel(root)

    items: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(item for item in dirnames if item not in SKIP_DIRS)
        for filename in sorted(filenames):
            items.append(_rel(Path(dirpath) / filename))
            if len(items) >= MAX_LIST_ITEMS:
                items.append("… список обрезан, показана вся просмотренная часть дерева")
                return "\n".join(items)
    if not items:
        return "(пусто)"
    return "\n".join(items)


def _resolve_file(path: str) -> Path:
    target = safe_path(path)
    if target.is_file():
        return target
    matches = _find_all_by_name(path)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        listed = "\n".join(f"- {_rel(item)}" for item in matches)
        raise WorkspaceError(
            f"Файл «{Path(path).name}» найден в нескольких местах. "
            f"Укажите какой:\n{listed}"
        )
    raise WorkspaceError(f"Файл не найден: {path}")


def read_file(path: str, offset: int = 1, limit: int = 200) -> str:
    target = _resolve_file(path)
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
    if target.is_dir() or any(target == root for root in trusted.all_roots()):
        raise WorkspaceError("Нельзя перезаписать корень папки")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not trusted.is_inside_trusted(target):
        raise WorkspaceError("Путь вне доверенных папок")
    target.write_text(content, encoding="utf-8")
    return f"Записано: {_rel(target)} ({len(content)} символов)"


def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
) -> str:
    needle = old_text or ""
    if not needle:
        raise WorkspaceError("Пустой old_text — укажи точный фрагмент из файла")
    if isinstance(replace_all, str):
        replace_all = replace_all.strip().lower() in {"1", "true", "yes", "да"}
    else:
        replace_all = bool(replace_all)
    target = _resolve_file(path)
    if b"\x00" in target.read_bytes()[:4096]:
        raise WorkspaceError("Бинарный файл менять нельзя")
    current = target.read_text(encoding="utf-8")
    count = current.count(needle)
    if count == 0:
        raise WorkspaceError(
            "Фрагмент old_text в файле не найден. Сначала read_file и скопируй текст как есть."
        )
    if count > 1 and not replace_all:
        raise WorkspaceError(
            f"Фрагмент встречается {count} раз. Уточни old_text или поставь replace_all=true."
        )
    if replace_all:
        updated = current.replace(needle, new_text or "")
    else:
        updated = current.replace(needle, new_text or "", 1)
    if not trusted.is_inside_trusted(target):
        raise WorkspaceError("Путь вне доверенных папок")
    target.write_text(updated, encoding="utf-8")
    changed = count if replace_all else 1
    return f"Изменено: {_rel(target)} ({changed} фрагмент)"


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
