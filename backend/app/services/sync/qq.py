import hashlib
import json
from pathlib import Path

import aiosqlite

from app.core.database import DB_PATH
from app.core.time_utils import normalize_timestamp


async def import_qq_json(file_path: str) -> dict:
    """Import QQ messages from JSON exported by common QQ export tools.

    The exporter ecosystem is not standardized, so this accepts either a list of
    messages or an object with `messages`/`data` plus optional chat metadata.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    chats = _extract_chats(raw, path)
    return await _import_chats(chats)


async def import_qq_dir(dir_path: str) -> dict:
    p = Path(dir_path)
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    files = sorted(p.rglob("*.json"))
    total = 0
    imported = 0
    chats = 0

    for file in files:
        try:
            result = await import_qq_json(str(file))
        except Exception:
            continue
        total += result["total"]
        imported += result["imported"]
        chats += result["chats"]

    return {"total": total, "imported": imported, "chats": chats, "files": len(files)}


def _extract_chats(raw: object, path: Path) -> list[dict]:
    if isinstance(raw, list):
        return [{
            "chat_name": path.stem,
            "chat_id": f"qq_{path.stem}",
            "chat_type": "group",
            "messages": raw,
        }]

    if not isinstance(raw, dict):
        return []

    if isinstance(raw.get("chats"), list):
        return [
            {
                "chat_name": chat.get("name") or chat.get("chat_name") or path.stem,
                "chat_id": str(chat.get("id") or chat.get("chat_id") or f"qq_{path.stem}"),
                "chat_type": _chat_type(chat),
                "messages": chat.get("messages") or chat.get("data") or [],
            }
            for chat in raw["chats"]
            if isinstance(chat, dict)
        ]

    messages = raw.get("messages") or raw.get("data") or raw.get("records") or []
    return [{
        "chat_name": raw.get("name") or raw.get("chat_name") or raw.get("group_name") or path.stem,
        "chat_id": str(raw.get("id") or raw.get("chat_id") or raw.get("group_id") or f"qq_{path.stem}"),
        "chat_type": _chat_type(raw),
        "messages": messages if isinstance(messages, list) else [],
    }]


async def _import_chats(chats: list[dict]) -> dict:
    total = 0
    imported = 0

    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        for chat in chats:
            chat_id = str(chat["chat_id"])
            chat_name = str(chat["chat_name"])
            chat_type = chat["chat_type"]
            messages = chat["messages"]
            total += len(messages)

            last_ts = None
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                content = _content(msg)
                timestamp = _timestamp(msg)
                if not timestamp or not content.strip():
                    continue

                sender_id = str(msg.get("sender_id") or msg.get("uin") or msg.get("user_id") or msg.get("qq") or "")
                sender_name = str(msg.get("sender_name") or msg.get("nickname") or msg.get("sender") or msg.get("name") or "")
                msg_type = str(msg.get("msg_type") or msg.get("type") or "text")
                source_id = _source_id(msg, chat_id, timestamp, sender_id, content)

                cursor = await db.execute(
                    """INSERT OR IGNORE INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "qq",
                        chat_id,
                        chat_name,
                        chat_type,
                        sender_id,
                        sender_name,
                        content,
                        msg_type,
                        timestamp,
                        source_id,
                        json.dumps(msg, ensure_ascii=False),
                    ),
                )
                imported += max(cursor.rowcount, 0)
                if last_ts is None or timestamp > last_ts:
                    last_ts = timestamp

            if last_ts:
                await db.execute(
                    """INSERT OR REPLACE INTO sync_state (platform, chat_id, last_timestamp, updated_at)
                       VALUES ('qq', ?, ?, CURRENT_TIMESTAMP)""",
                    (chat_id, last_ts),
                )

            # Commit per chat so concurrent syncs (e.g. /sync/all) don't sit
            # behind a long-held WAL writer lock.
            await db.commit()

    return {"total": total, "imported": imported, "chats": len(chats)}


def _chat_type(chat: dict) -> str:
    raw = str(chat.get("type") or chat.get("chat_type") or "").lower()
    if raw in {"private", "friend", "personal"}:
        return "private"
    return "group"


def _content(msg: dict) -> str:
    value = msg.get("content") or msg.get("text") or msg.get("message") or ""
    if isinstance(value, list):
        return "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in value)
    return str(value)


def _timestamp(msg: dict) -> str | None:
    value = msg.get("timestamp") or msg.get("time") or msg.get("date") or msg.get("datetime")
    return normalize_timestamp(value)


def _source_id(msg: dict, chat_id: str, timestamp: str, sender_id: str, content: str) -> str:
    raw_id = msg.get("id") or msg.get("msg_id") or msg.get("message_id") or msg.get("seq")
    if raw_id is not None:
        return str(raw_id)
    fingerprint = f"{chat_id}|{timestamp}|{sender_id}|{content}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()
