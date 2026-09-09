from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import httpx

from app.config import settings

_lock = threading.Lock()
_playwright = None
_browser = None


def _chrome_exe() -> Path:
    roots = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LocalAppData", ""),
    ]
    for root in roots:
        if not root:
            continue
        candidate = Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if candidate.is_file():
            return candidate
    raise RuntimeError("Google Chrome не найден. Установите его на RDP.")


def _cdp_ready() -> bool:
    url = settings.browser_cdp_url.rstrip("/") + "/json/version"
    try:
        response = httpx.get(url, timeout=1.5)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _launch_chrome() -> None:
    if _cdp_ready():
        return
    exe = _chrome_exe()
    port = urlparse(settings.browser_cdp_url).port or 9222
    profile = settings.chrome_profile_dir
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            str(exe),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(40):
        time.sleep(0.5)
        if _cdp_ready():
            return
    raise RuntimeError(
        f"Chrome не открыл отладку на {settings.browser_cdp_url}. "
        f"Запускал {exe} с профилем {profile}."
    )


def _playwright_connect():
    global _playwright, _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Нет пакета playwright. На RDP: pip install playwright") from exc
    if _playwright is None:
        _playwright = sync_playwright().start()
    _browser = _playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
    return _browser


def _connect():
    global _browser
    if _browser is not None:
        try:
            _ = _browser.contexts
            return _browser
        except Exception:
            _browser = None
    last: Exception | None = None
    for _ in range(2):
        try:
            if not _cdp_ready():
                _launch_chrome()
            return _playwright_connect()
        except Exception as exc:
            last = exc
            _browser = None
    raise RuntimeError(
        "Не удалось подключить Chrome агента. "
        f"{last}. Нужны Google Chrome и pip install playwright. "
        "Не запускайте chrome-debug.ps1 через Блокнот — вызовите browser_start."
    ) from last


def _page():
    browser = _connect()
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("В Chrome нет окон.")
    pages = contexts[0].pages
    if not pages:
        raise RuntimeError("В Chrome нет вкладок.")
    return pages[-1]


def _clip(text: str, limit: int = 10_000) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n… текст страницы обрезан"
    return text or "(пусто)"


def _status_text() -> str:
    browser = _connect()
    pages = []
    for ctx in browser.contexts:
        pages.extend(ctx.pages)
    lines = [
        "Chrome подключён "
        f"(профиль агента: {settings.chrome_profile_dir}). "
        f"Вкладок: {len(pages)}"
    ]
    for index, page in enumerate(pages, start=1):
        title = page.title() or "без названия"
        lines.append(f"{index}. {title} — {page.url}")
    return "\n".join(lines)


def browser_start() -> str:
    with _lock:
        return _status_text()


def browser_status() -> str:
    with _lock:
        return _status_text()


def browser_tabs() -> str:
    return browser_status()


def browser_goto(url: str) -> str:
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")):
        raise ValueError("Нужен адрес http/https")
    with _lock:
        page = _page()
        page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(800)
        return f"Открыто: {page.title()}\n{page.url}"


def browser_content() -> str:
    with _lock:
        page = _page()
        title = page.title()
        url = page.url
        try:
            body = page.inner_text("body", timeout=15_000)
        except Exception:
            body = page.content()
        return _clip(f"{title}\n{url}\n\n{body}")


def browser_click(text: str) -> str:
    needle = (text or "").strip()
    if not needle:
        raise ValueError("Пустой текст кнопки")
    with _lock:
        page = _page()
        locator = page.get_by_text(needle, exact=False).first
        locator.click(timeout=15_000)
        page.wait_for_timeout(700)
        return f"Клик по «{needle}». Сейчас: {page.title()} — {page.url}"


def browser_type(text: str, field: str = "") -> str:
    value = text or ""
    with _lock:
        page = _page()
        if field.strip():
            page.get_by_text(field.strip(), exact=False).first.click(timeout=10_000)
        page.keyboard.type(value, delay=20)
        return f"Введено {len(value)} символов."


def browser_search(query: str) -> str:
    text = (query or "").strip()
    if not text:
        raise ValueError("Пустой поисковый запрос")
    url = f"https://yandex.ru/search/?text={quote_plus(text)}"
    with _lock:
        page = _page()
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1500)
        title = page.title()
        try:
            body = page.inner_text("body", timeout=15_000)
        except Exception:
            body = page.content()
        return _clip(
            f"Поиск в Chrome (Яндекс): {text}\n{title}\n{page.url}\n\n{body}"
        )


def browser_press(key: str = "Enter") -> str:
    with _lock:
        page = _page()
        page.keyboard.press((key or "Enter").strip())
        page.wait_for_timeout(700)
        return f"Клавиша {key}. Сейчас: {page.url}"
