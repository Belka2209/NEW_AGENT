from __future__ import annotations

import json
import re
from typing import Any

KNOWN_TOOLS = {
    "list_files",
    "read_file",
    "write_file",
    "search_files",
    "run_command",
    "web_search",
    "current_datetime",
}


def _as_call(name: str, args: Any, index: int) -> dict[str, Any] | None:
    if name not in KNOWN_TOOLS:
        return None
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None
    return {
        "id": f"call_{name}_{index}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False),
        },
    }


def _from_obj(obj: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or ""
    args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
    if not name and isinstance(obj.get("function"), dict):
        name = obj["function"].get("name") or ""
        args = obj["function"].get("arguments") or args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"_raw": args}
    return _as_call(str(name), args, index)


def _extract_objects(text: str) -> list[tuple[Any, int, int]]:
    decoder = json.JSONDecoder()
    found: list[tuple[Any, int, int]] = []
    idx = 0
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        found.append((obj, start, end))
        idx = end
    return found


def parse_text_tool_calls(text: str) -> tuple[list[dict[str, Any]], str]:
    if not (text or "").strip():
        return [], ""

    cleaned = re.sub(r"</?tool_call>", "", text)
    objects = _extract_objects(cleaned)
    calls: list[dict[str, Any]] = []
    spans: list[tuple[int, int]] = []

    for obj, start, end in objects:
        if isinstance(obj, list):
            for item in obj:
                parsed = _from_obj(item, len(calls))
                if parsed:
                    calls.append(parsed)
                    spans.append((start, end))
            continue
        parsed = _from_obj(obj, len(calls))
        if parsed:
            calls.append(parsed)
            spans.append((start, end))

    leftover = cleaned
    for start, end in sorted(spans, reverse=True):
        leftover = leftover[:start] + leftover[end:]
    leftover = re.sub(r"```(?:json)?", "", leftover)
    leftover = re.sub(r"\n{3,}", "\n\n", leftover).strip()
    return calls, leftover


def looks_like_tool_json(text: str) -> bool:
    calls, leftover = parse_text_tool_calls(text)
    return bool(calls) and not leftover
