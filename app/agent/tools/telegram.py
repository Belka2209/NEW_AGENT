from __future__ import annotations

import httpx

from app.config import settings


def _token() -> str:
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        raise RuntimeError(
            "Telegram не настроен. Создайте бота в @BotFather, "
            "добавьте TELEGRAM_BOT_TOKEN в .env и перезапустите сервер."
        )
    return token


def _api(method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{_token()}/{method}"
    response = httpx.post(url, json=payload or {}, timeout=20.0)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or str(data))
    return data["result"]


def parse_chat_aliases() -> dict[str, str]:
    raw = settings.telegram_chats or ""
    aliases: dict[str, str] = {}
    for part in raw.split(","):
        item = part.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        name, value = name.strip(), value.strip()
        if name and value:
            aliases[name.lower()] = value
    return aliases


def resolve_chat(chat: str) -> str:
    text = (chat or "").strip()
    if not text:
        raise ValueError("Не указан чат")
    aliases = parse_chat_aliases()
    return aliases.get(text.lower(), text)


def telegram_status() -> str:
    me = _api("getMe")
    username = me.get("username") or "?"
    name = me.get("first_name") or ""
    aliases = parse_chat_aliases()
    lines = [f"Бот @{username} ({name}) подключён."]
    if aliases:
        lines.append("Чат-алиасы из .env:")
        for name, chat_id in aliases.items():
            lines.append(f"- {name} → {chat_id}")
    else:
        lines.append(
            "Алиасов нет. Напишите боту /start или добавьте его в группу, "
            "затем вызовите telegram_chats и пропишите TELEGRAM_CHATS в .env."
        )
    return "\n".join(lines)


def telegram_chats() -> str:
    aliases = parse_chat_aliases()
    lines = []
    if aliases:
        lines.append("Алиасы:")
        for name, chat_id in aliases.items():
            lines.append(f"- {name} → {chat_id}")
        lines.append("")

    updates = _api("getUpdates", {"limit": 50, "timeout": 0})
    seen: dict[str, str] = {}
    for item in updates:
        msg = item.get("message") or item.get("channel_post") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        title = chat.get("title") or " ".join(
            part for part in [chat.get("first_name"), chat.get("last_name")] if part
        ) or chat.get("username") or str(chat_id)
        kind = chat.get("type") or "?"
        seen[str(chat_id)] = f"{title} ({kind})"

    if seen:
        lines.append("Недавние чаты из getUpdates:")
        for chat_id, title in seen.items():
            lines.append(f"- {chat_id} — {title}")
    else:
        lines.append(
            "Недавних чатов нет. Напишите боту в личку /start "
            "или добавьте его в группу и отправьте туда любое сообщение."
        )
    return "\n".join(lines)


def telegram_send(chat: str, text: str) -> str:
    body = (text or "").strip()
    if not body:
        raise ValueError("Пустой текст сообщения")
    chat_id = resolve_chat(chat)
    result = _api("sendMessage", {"chat_id": chat_id, "text": body})
    dest = result.get("chat") or {}
    name = dest.get("title") or dest.get("username") or dest.get("first_name") or chat_id
    return f"Отправлено в {name} ({dest.get('id', chat_id)})"
