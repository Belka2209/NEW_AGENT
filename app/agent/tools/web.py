from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote, urlparse

import httpx


def web_search(query: str, max_results: int = 5) -> str:
    text = (query or "").strip()
    if not text:
        raise ValueError("Пустой поисковый запрос")

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("Пакет ddgs не установлен") from exc

    limit = max(5, min(int(max_results or 5), 8))
    with DDGS() as client:
        rows = list(client.text(text, max_results=limit))

    if not rows:
        return "Ничего не найдено. Для погоды используй get_weather."

    lines = [
        "Это только заголовки и короткие сниппеты, не полные статьи.",
        "Цифр погоды здесь обычно нет — для погоды вызови get_weather.",
        "",
    ]
    for index, row in enumerate(rows, start=1):
        title = row.get("title") or "без названия"
        href = row.get("href") or row.get("url") or ""
        body = (row.get("body") or "").strip()
        lines.append(f"{index}. {title}\n{href}\n{body}")
    return "\n\n".join(lines)


def fetch_url(url: str) -> str:
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Нужен обычный http/https адрес")

    response = httpx.get(
        raw,
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "LocalAgent/1.0"},
    )
    response.raise_for_status()
    content_type = (response.headers.get("content-type") or "").lower()
    text = response.text
    if "html" in content_type or text.lstrip().startswith("<"):
        text = _html_to_text(text)
    text = text.strip() or "(пустая страница)"
    if len(text) > 8_000:
        text = text[:8_000] + "\n… страница обрезана"
    return text


_CITY_ALIASES = {
    "санкт-петербург": "Saint Petersburg",
    "санкт петербург": "Saint Petersburg",
    "петербург": "Saint Petersburg",
    "питер": "Saint Petersburg",
    "спб": "Saint Petersburg",
}


def get_weather(city: str) -> str:
    raw_place = (city or "").strip() or "Saint Petersburg"
    place = _CITY_ALIASES.get(raw_place.lower(), raw_place)
    url = f"https://wttr.in/{quote(place)}?format=j1&lang=ru"
    response = httpx.get(
        url,
        timeout=20.0,
        follow_redirects=True,
        headers={"User-Agent": "LocalAgent/1.0"},
    )
    response.raise_for_status()
    data = response.json()
    current = (data.get("current_condition") or [{}])[0]
    day = (data.get("weather") or [{}])[0]
    nearest = ((data.get("nearest_area") or [{}])[0].get("areaName") or [{}])[0]
    nearest_name = nearest.get("value") or ""
    name = raw_place
    if nearest_name and nearest_name.lower() not in raw_place.lower():
        name = f"{raw_place} ({nearest_name})"

    desc_list = current.get("lang_ru") or current.get("weatherDesc") or []
    desc = (desc_list[0] or {}).get("value") or "нет описания"

    return (
        f"Город: {name}\n"
        f"Сейчас: {current.get('temp_C', '?')}°C, ощущается как {current.get('FeelsLikeC', '?')}°C\n"
        f"Небо: {desc}\n"
        f"Ветер: {current.get('windspeedKmph', '?')} км/ч, влажность {current.get('humidity', '?')}%\n"
        f"Сегодня: мин {day.get('mintempC', '?')}°C, макс {day.get('maxtempC', '?')}°C"
    )


def _html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|nav|footer|noscript).*?>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</p>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
