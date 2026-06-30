import json
from pathlib import Path

import aiosqlite

from app.core.database import DB_PATH
from app.core.time_utils import normalize_timestamp


async def import_telegram_json(file_path: str) -> dict:
    """Import Telegram chat history from official export JSON.

    Telegram Desktop export produces result.json with structure:
    {
      "name": "Chat Name",
      "type": "personal_group" | "personal_chat" | ...,
      "id": 12345,
      "messages": [...]
    }

    Returns {"total": N, "imported": N, "chats": N}.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    raw = json.loads(path.read_text(encoding="utf-8"))

    # Full export format: {"chats": {"list": [...]}}
    if "chats" in raw and isinstance(raw["chats"].get("list"), list):
        return await _import_full_export(raw["chats"]["list"])

    # Single chat format: {"name": "...", "messages": [...]}
    chat_list = [raw]
    return await _import_full_export(chat_list)


async def _import_full_export(chat_list: list[dict]) -> dict:
    total_imported = 0
    total_raw = 0

    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        for chat in chat_list:
            chat_name = chat.get("name", "")
            chat_id = str(chat.get("id", f"tg_{chat_name}"))
            chat_type = _map_chat_type(chat.get("type", ""))
            messages = chat.get("messages", [])
            total_raw += len(messages)

            for msg in messages:
                if msg.get("type") == "service":
                    continue

                content = _extract_content(msg)
                ts = normalize_timestamp(msg.get("date"))
                sender_id = str(msg.get("from_id", msg.get("actor_id", "")))
                sender_name = str(msg.get("from", msg.get("actor", "")))
                msg_type = _map_msg_type(msg.get("media_type"), msg.get("type"), msg.get("sticker_emoji"))

                if not ts:
                    continue

                if msg_type == "text" and not content.strip():
                    continue

                cursor = await db.execute(
                    """INSERT OR IGNORE INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "telegram",
                        chat_id,
                        chat_name,
                        chat_type,
                        sender_id,
                        sender_name,
                        content,
                        msg_type,
                        ts,
                        _source_id(msg, ts, sender_id, content),
                        json.dumps(msg, ensure_ascii=False),
                    ),
                )
                total_imported += max(cursor.rowcount, 0)

            if messages:
                await db.execute(
                    """INSERT OR REPLACE INTO sync_state (platform, chat_id, last_timestamp, updated_at)
                       VALUES ('telegram', ?, ?, CURRENT_TIMESTAMP)""",
                    (chat_id, messages[-1].get("date")),
                )

        await db.commit()

    return {"total": total_raw, "imported": total_imported, "chats": len(chat_list)}


async def import_telegram_dir(dir_path: str) -> dict:
    """Import all result.json from Telegram export directory.

    Telegram Desktop exports create one folder per chat, each with a result.json.
    """
    p = Path(dir_path)
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    # Find all result.json recursively
    files = list(p.rglob("result.json"))
    if not files:
        # Maybe the user pointed to a single result.json
        if p.name == "result.json":
            files = [p]
        else:
            return {"total": 0, "imported": 0, "chats": 0, "files": 0}

    total_imported = 0
    total_raw = 0
    chat_count = 0

    for f in files:
        try:
            result = await import_telegram_json(str(f))
            total_imported += result["imported"]
            total_raw += result["total"]
            chat_count += result["chats"]
        except Exception:
            continue

    return {"total": total_raw, "imported": total_imported, "chats": chat_count, "files": len(files)}


def _extract_content(msg: dict) -> str:
    """Extract text content from Telegram message."""
    text = msg.get("text", "")
    if isinstance(text, str):
        return text
    # Telegram export may use nested text entities
    if isinstance(text, list):
        parts = []
        for part in text:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(part.get("text", ""))
        return "".join(parts)
    return str(text)


def _map_chat_type(tg_type: str) -> str:
    mapping = {
        "personal_chat": "private",
        "personal_group": "group",
        "bot_chat": "private",
        "channel": "channel",
    }
    return mapping.get(tg_type, "group")


def _map_msg_type(media_type: str | None, msg_type: str | None, sticker: str | None) -> str:
    if sticker:
        return "sticker"
    if media_type:
        mapping = {
            "sticker": "sticker",
            "animation": "sticker",
            "voice_message": "voice",
            "video_message": "video",
            "photo": "image",
            "video_file": "video",
            "document": "file",
        }
        return mapping.get(media_type, "text")
    if msg_type == "link":
        return "link"
    return "text"


def _source_id(msg: dict, ts: str, sender_id: str, content: str) -> str:
    raw_id = msg.get("id")
    if raw_id is not None:
        return str(raw_id)
    return f"{ts}:{sender_id}:{content[:120]}"
