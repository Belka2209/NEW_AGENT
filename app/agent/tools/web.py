from __future__ import annotations


def web_search(query: str, max_results: int = 5) -> str:
    text = (query or "").strip()
    if not text:
        raise ValueError("Пустой поисковый запрос")

    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise RuntimeError("Пакет ddgs не установлен") from exc

    limit = max(1, min(int(max_results or 5), 8))
    with DDGS() as client:
        rows = list(client.text(text, max_results=limit))

    if not rows:
        return "Ничего не найдено"

    lines = []
    for index, row in enumerate(rows, start=1):
        title = row.get("title") or "без названия"
        href = row.get("href") or row.get("url") or ""
        body = (row.get("body") or "").strip()
        lines.append(f"{index}. {title}\n{href}\n{body}")
    return "\n\n".join(lines)
