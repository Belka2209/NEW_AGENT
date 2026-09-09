from __future__ import annotations

import re
from html import unescape
from urllib.parse import quote, urlparse

import httpx

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


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
        try:
            from app.agent.tools.browser import browser_search

            extra = browser_search(text)
            return (
                "Веб-поиск ничего не дал. Открыл тот же запрос в Chrome:\n\n" + extra
            )
        except Exception as exc:
            return (
                "Веб-поиск ничего не нашёл. В Chrome тоже не вышло: "
                f"{exc}. Скажите агенту «открой браузер» или проверьте, что установлен Google Chrome."
            )

    lines = [
        "Результаты поиска. Если здесь нет нужных фактов — вызови browser_search с тем же запросом.",
        "",
    ]
    opened = 0
    for index, row in enumerate(rows, start=1):
        title = row.get("title") or "без названия"
        href = row.get("href") or row.get("url") or ""
        body = (row.get("body") or "").strip()
        block = [f"{index}. {title}", href, body]
        if href and opened < 2:
            preview = _fetch_preview(href, limit=1600)
            if preview:
                block.append(preview)
                opened += 1
        lines.append("\n".join(part for part in block if part))
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
        headers=_HEADERS,
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

_CITY_COORDS = {
    "saint petersburg": (59.9343, 30.3351, "Санкт-Петербург"),
    "санкт-петербург": (59.9343, 30.3351, "Санкт-Петербург"),
    "санкт петербург": (59.9343, 30.3351, "Санкт-Петербург"),
    "петербург": (59.9343, 30.3351, "Санкт-Петербург"),
    "питер": (59.9343, 30.3351, "Санкт-Петербург"),
    "спб": (59.9343, 30.3351, "Санкт-Петербург"),
    "москва": (55.7558, 37.6173, "Москва"),
    "moscow": (55.7558, 37.6173, "Москва"),
}

_WMO = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь",
    51: "лёгкая морось",
    53: "морось",
    55: "сильная морось",
    61: "небольшой дождь",
    63: "дождь",
    65: "сильный дождь",
    71: "небольшой снег",
    73: "снег",
    75: "сильный снег",
    80: "ливень",
    81: "ливень",
    82: "сильный ливень",
    85: "снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с градом",
}


def _fetch_preview(url: str, limit: int = 1600) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        response = httpx.get(url, timeout=8.0, follow_redirects=True, headers=_HEADERS)
        response.raise_for_status()
        text = response.text
        if "html" in (response.headers.get("content-type") or "").lower() or text.lstrip().startswith("<"):
            text = _html_to_text(text)
        text = text.strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        return text
    except Exception:
        return ""


def _http_get_json(url: str) -> dict:
    response = httpx.get(url, timeout=15.0, follow_redirects=True, headers=_HEADERS)
    response.raise_for_status()
    return response.json()


def _resolve_city(city: str) -> tuple[float, float, str]:
    raw = (city or "").strip() or "Санкт-Петербург"
    known = _CITY_COORDS.get(raw.lower())
    if known:
        return known
    query = _CITY_ALIASES.get(raw.lower(), raw)
    data = _http_get_json(
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote(query)}&count=1&language=ru"
    )
    rows = data.get("results") or []
    if not rows:
        raise RuntimeError(f"город не найден: {raw}")
    row = rows[0]
    return float(row["latitude"]), float(row["longitude"]), row.get("name") or raw


def _weather_open_meteo(city: str) -> str:
    lat, lon, name = _resolve_city(city)
    data = _http_get_json(
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        "wind_speed_10m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&timezone=auto"
    )
    current = data.get("current") or {}
    daily = data.get("daily") or {}
    code = int(current.get("weather_code") or 0)
    desc = _WMO.get(code, f"код {code}")
    mins = daily.get("temperature_2m_min") or ["?"]
    maxs = daily.get("temperature_2m_max") or ["?"]
    return (
        f"Город: {name}\n"
        f"Сейчас: {current.get('temperature_2m', '?')}°C, "
        f"ощущается как {current.get('apparent_temperature', '?')}°C\n"
        f"Небо: {desc}\n"
        f"Ветер: {current.get('wind_speed_10m', '?')} км/ч, "
        f"влажность {current.get('relative_humidity_2m', '?')}%\n"
        f"Сегодня: мин {mins[0]}°C, макс {maxs[0]}°C"
    )


def _weather_wttr(city: str) -> str:
    raw_place = (city or "").strip() or "Saint Petersburg"
    place = _CITY_ALIASES.get(raw_place.lower(), raw_place)
    data = _http_get_json(f"https://wttr.in/{quote(place)}?format=j1&lang=ru")
    current = (data.get("current_condition") or [{}])[0]
    day = (data.get("weather") or [{}])[0]
    desc_list = current.get("lang_ru") or current.get("weatherDesc") or []
    desc = (desc_list[0] or {}).get("value") or "нет описания"
    return (
        f"Город: {raw_place}\n"
        f"Сейчас: {current.get('temp_C', '?')}°C, ощущается как {current.get('FeelsLikeC', '?')}°C\n"
        f"Небо: {desc}\n"
        f"Ветер: {current.get('windspeedKmph', '?')} км/ч, влажность {current.get('humidity', '?')}%\n"
        f"Сегодня: мин {day.get('mintempC', '?')}°C, макс {day.get('maxtempC', '?')}°C"
    )


def get_weather(city: str) -> str:
    errors: list[str] = []
    for loader in (_weather_open_meteo, _weather_wttr):
        try:
            return loader(city)
        except Exception as exc:
            errors.append(f"{loader.__name__}: {exc}")
    return "Не удалось получить погоду:\n" + "\n".join(errors)


def _html_to_text(html: str) -> str:
    cleaned = re.sub(r"(?is)<(script|style|nav|footer|noscript).*?>.*?</\1>", " ", html)
    cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
    cleaned = re.sub(r"(?is)</p>", "\n", cleaned)
    cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
