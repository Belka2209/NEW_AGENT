from __future__ import annotations

import subprocess
from pathlib import Path

from app import trusted
from app.agent.tools.files import safe_path


def _git_root_from(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for _ in range(16):
        if (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    raise ValueError("Git-репозиторий не найден рядом с этим путём")


def _find_repo(path: str = "") -> Path:
    raw = (path or "").strip()
    if raw:
        return _git_root_from(safe_path(raw))
    last_error = "Git-репозиторий не найден"
    for root in trusted.all_roots():
        try:
            return _git_root_from(root)
        except ValueError as exc:
            last_error = str(exc)
    raise ValueError(last_error + ". Передай path корня проекта.")


def _run_git(args: list[str], path: str = "") -> str:
    root = _find_repo(path)
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=20,
        encoding="utf-8",
        errors="replace",
    )
    output = ((completed.stdout or "") + (completed.stderr or "")).strip() or "(нет вывода)"
    if len(output) > 20_000:
        output = output[:20_000] + "\n… вывод обрезан"
    if completed.returncode != 0:
        return f"{root}\nexit={completed.returncode}\n{output}"
    return f"{root}\n{output}"


def git_status(path: str = "") -> str:
    return _run_git(["status", "--short", "--branch"], path)


def git_diff(path: str = "") -> str:
    stat = _run_git(["diff", "--stat", "HEAD"], path)
    body = _run_git(["diff", "HEAD"], path)
    text = stat + "\n\n" + body
    if len(text) > 20_000:
        return text[:20_000] + "\n… diff обрезан"
    return text


def git_log(path: str = "") -> str:
    return _run_git(["log", "-8", "--oneline"], path)
