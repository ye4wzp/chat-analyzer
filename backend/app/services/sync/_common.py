"""Shared helpers for platform sync services (qq_qce, telegram_live, ...)."""

import hashlib
import json
from datetime import datetime, timezone

import aiosqlite


def parse_ts(value: str | int | float | None) -> str | None:
    """Normalize a timestamp from any plausible source into ISO 8601."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        # Heuristic: treat 13-digit values as milliseconds.
        if ts > 1e12:
            ts /= 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone().isoformat()
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return s


def source_id(raw: object, *parts: object) -> str:
    """Use the platform's own message id when present, else sha1 of fingerprint."""
    if raw not in (None, "", 0):
        return str(raw)
    fingerprint = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


async def upsert_message(
    db: aiosqlite.Connection,
    *,
    platform: str,
    chat_id: str,
    chat_name: str,
    chat_type: str,
    sender_id: str,
    sender_name: str,
    content: str,
    msg_type: str,
    timestamp: str,
    src_id: str,
    raw: object,
) -> int:
    """Insert one message; rows already present are ignored. Returns 0 or 1."""
    cursor = await db.execute(
        """INSERT OR IGNORE INTO messages
           (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
            content, msg_type, timestamp, source_id, raw_data)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            platform, chat_id, chat_name, chat_type,
            sender_id, sender_name, content, msg_type, timestamp,
            src_id, json.dumps(raw, ensure_ascii=False, default=str),
        ),
    )
    return max(cursor.rowcount, 0)


async def update_sync_state(
    db: aiosqlite.Connection,
    *,
    platform: str,
    chat_id: str,
    last_timestamp: str | None = None,
    last_msg_id: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO sync_state (platform, chat_id, last_msg_id, last_timestamp, updated_at)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(platform, chat_id) DO UPDATE SET
             last_msg_id=COALESCE(excluded.last_msg_id, sync_state.last_msg_id),
             last_timestamp=COALESCE(excluded.last_timestamp, sync_state.last_timestamp),
             updated_at=CURRENT_TIMESTAMP""",
        (platform, chat_id, last_msg_id, last_timestamp),
    )


async def read_sync_state(
    db: aiosqlite.Connection, platform: str, chat_id: str,
) -> tuple[str | None, str | None]:
    """Return (last_msg_id, last_timestamp)."""
    cursor = await db.execute(
        "SELECT last_msg_id, last_timestamp FROM sync_state WHERE platform=? AND chat_id=?",
        (platform, chat_id),
    )
    row = await cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)
