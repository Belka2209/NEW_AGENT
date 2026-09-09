from __future__ import annotations

import httpx

_HH_HEADERS = {
    "User-Agent": "LocalAgent/1.0 (hh.ru vacancy search)",
    "HH-User-Agent": "LocalAgent/1.0",
}

_AREAS = {
    "санкт-петербург": "2",
    "санкт петербург": "2",
    "петербург": "2",
    "питер": "2",
    "спб": "2",
    "москва": "1",
    "moscow": "1",
    "россия": "113",
}


def hh_search(query: str, city: str = "Санкт-Петербург", per_page: int = 8) -> str:
    text = (query or "").strip()
    if not text:
        raise ValueError("Пустой запрос вакансии")
    area = _AREAS.get((city or "").strip().lower(), "")
    params: dict[str, str | int] = {
        "text": text,
        "per_page": max(3, min(int(per_page or 8), 15)),
        "order_by": "relevance",
    }
    if area:
        params["area"] = area

    response = httpx.get(
        "https://api.hh.ru/vacancies",
        params=params,
        headers=_HH_HEADERS,
        timeout=20.0,
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("items") or []
    if not items:
        return f"На hh.ru ничего не найдено по запросу «{text}»."

    lines = [f"Вакансии hh.ru по «{text}» ({city or 'все регионы'}):", ""]
    for index, item in enumerate(items, start=1):
        name = item.get("name") or "без названия"
        employer = ((item.get("employer") or {}).get("name")) or "компания не указана"
        place = ((item.get("area") or {}).get("name")) or ""
        url = item.get("alternate_url") or ""
        salary = _salary(item.get("salary"))
        snippet = item.get("snippet") or {}
        req = _plain(snippet.get("requirement"))
        resp = _plain(snippet.get("responsibility"))
        block = [f"{index}. {name} — {employer}", place, salary]
        if req:
            block.append("Требования: " + req)
        if resp:
            block.append("Обязанности: " + resp)
        if url:
            block.append(url)
        lines.append("\n".join(part for part in block if part))
    return "\n\n".join(lines)


def _salary(raw: dict | None) -> str:
    if not raw:
        return "зарплата не указана"
    cur = raw.get("currency") or "RUR"
    low, high = raw.get("from"), raw.get("to")
    if low and high:
        return f"от {low} до {high} {cur}"
    if low:
        return f"от {low} {cur}"
    if high:
        return f"до {high} {cur}"
    return "зарплата не указана"


def _plain(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("<highlighttext>", "").replace("</highlighttext>", "").strip()
