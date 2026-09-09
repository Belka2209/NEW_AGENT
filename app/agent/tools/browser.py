from __future__ import annotations

import threading

from app.config import settings

_lock = threading.Lock()
_playwright = None
_browser = None


def _connect():
    global _playwright, _browser
    if _browser is not None:
        try:
            _ = _browser.contexts
            return _browser
        except Exception:
            _browser = None
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Нет пакета playwright. На RDP: pip install playwright"
        ) from exc

    if _playwright is None:
        _playwright = sync_playwright().start()
    try:
        _browser = _playwright.chromium.connect_over_cdp(settings.browser_cdp_url)
    except Exception as exc:
        raise RuntimeError(
            "Chrome не доступен на "
            f"{settings.browser_cdp_url}. Закройте все окна Chrome и запустите "
            r".\chrome-debug.ps1 — затем откройте Bitrix или hh.ru и войдите."
        ) from exc
    return _browser


def _page():
    browser = _connect()
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("В Chrome нет окон. Откройте вкладку.")
    pages = contexts[0].pages
    if not pages:
        raise RuntimeError("В Chrome нет вкладок.")
    return pages[-1]


def _clip(text: str, limit: int = 10_000) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        return text[:limit] + "\n… текст страницы обрезан"
    return text or "(пусто)"


def browser_status() -> str:
    with _lock:
        browser = _connect()
        pages = []
        for ctx in browser.contexts:
            pages.extend(ctx.pages)
        lines = [f"Chrome подключён. Вкладок: {len(pages)}"]
        for index, page in enumerate(pages, start=1):
            title = page.title() or "без названия"
            lines.append(f"{index}. {title} — {page.url}")
        return "\n".join(lines)


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


def browser_press(key: str = "Enter") -> str:
    with _lock:
        page = _page()
        page.keyboard.press((key or "Enter").strip())
        page.wait_for_timeout(700)
        return f"Клавиша {key}. Сейчас: {page.url}"
