from __future__ import annotations

import re
import subprocess
import sys

from app.config import settings

BLOCKED = [
    r"\brm\s+-rf\s+[\\/]",
    r"\bformat\b",
    r"\bshutdown\b",
    r"\brestart-computer\b",
    r"\bstop-computer\b",
    r"\bdel\s+/s\b",
    r"\brd\s+/s\b",
    r"\bremove-item\s+.*-recurse\b",
    r"\breg\s+delete\b",
    r"\bcipher\s+/w\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
]


def run_command(command: str) -> str:
    text = (command or "").strip()
    if not text:
        raise ValueError("Пустая команда")
    lowered = text.lower()
    for pattern in BLOCKED:
        if re.search(pattern, lowered):
            raise ValueError("Команда заблокирована как опасная")

    timeout = settings.terminal_timeout
    cwd = str(settings.workspace_dir)
    if sys.platform == "win32":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", text]
    else:
        argv = ["bash", "-lc", text]

    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(f"Команда превысила {timeout} с") from exc

    output = (completed.stdout or "") + (completed.stderr or "")
    output = output.strip() or "(нет вывода)"
    if len(output) > 20_000:
        output = output[:20_000] + "\n… вывод обрезан"
    return f"exit={completed.returncode}\n{output}"
