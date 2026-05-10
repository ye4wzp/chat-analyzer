import asyncio
import hashlib
import json
from datetime import datetime

import aiosqlite

from app.core.database import get_db, DB_PATH
from app.core.config import ensure_dirs


async def _run_wx(*args: str) -> str:
    """Run wx-cli command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "wx", *args, "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"wx {' '.join(args)} failed: {stderr.decode()}")
    return stdout.decode()


def _parse_ts(ts_str: str | int | None) -> str | None:
    if not ts_str:
        return None
    if isinstance(ts_str, int):
        return datetime.fromtimestamp(ts_str).isoformat()
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.isoformat()
    except (ValueError, AttributeError):
        return str(ts_str)


def _source_id(msg: dict, chat_id: str, ts: str, content: str) -> str:
    raw_id = msg.get("id") or msg.get("msg_id") or msg.get("msgid") or msg.get("local_id")
    if raw_id:
        return str(raw_id)
    sender = msg.get("sender_id") or msg.get("talker") or ""
    fingerprint = f"{chat_id}|{ts}|{sender}|{content}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


async def sync_sessions() -> int:
    """Sync recent sessions from wx-cli. Returns count of new messages."""
    raw = await _run_wx("sessions", "-n", "500")
    sessions = json.loads(raw)
    if not sessions:
        return 0

    ensure_dirs()
    total = 0

    async with aiosqlite.connect(str(DB_PATH), timeout=30) as db:
        for session in sessions:
            chat_name = session.get("chat") or session.get("name") or session.get("nickname") or ""
            chat_id = session.get("username") or session.get("chat_id") or ""

            if not chat_id:
                continue

            # Pull messages for this session
            count = await _sync_chat_history(db, chat_id, chat_name)
            total += count
            # Commit per chat so a long history sync doesn't hold the WAL
            # writer lock against parallel QQ/Telegram syncs.
            await db.commit()

    return total


async def _sync_chat_history(
    db: aiosqlite.Connection, chat_id: str, chat_name: str, batch_size: int = 500
) -> int:
    """Sync message history for a single chat with pagination. Returns new message count."""

    cursor = await db.execute(
        "SELECT last_timestamp FROM sync_state WHERE platform='wechat' AND chat_id=?",
        (chat_id,),
    )
    row = await cursor.fetchone()
    since_ts = row[0] if row else None

    total_inserted = 0
    offset = 0
    last_ts = since_ts

    while True:
        args = ["history", chat_name if chat_name else chat_id, "-n", str(batch_size), "--offset", str(offset)]
        if since_ts:
            args.extend(["--since", str(since_ts)[:10]])

        try:
            raw = await _run_wx(*args)
        except RuntimeError:
            break

        messages = json.loads(raw)
        if not messages:
            break

        batch_inserted = 0
        for msg in messages:
            ts = _parse_ts(msg.get("timestamp") or msg.get("time") or msg.get("created_at"))
            if not ts:
                continue

            content = msg.get("content") or msg.get("text") or ""
            cursor = await db.execute(
                """INSERT OR IGNORE INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "wechat",
                    chat_id,
                    chat_name,
                    "group" if chat_id.endswith("@chatroom") else "private",
                    msg.get("sender_id") or msg.get("talker") or "",
                    msg.get("sender") or msg.get("sender_name") or msg.get("nickname") or "",
                    content,
                    msg.get("type", "text"),
                    ts,
                    _source_id(msg, chat_id, ts, content),
                    json.dumps(msg, ensure_ascii=False),
                ),
            )
            batch_inserted += max(cursor.rowcount, 0)
            if not last_ts or ts > last_ts:
                last_ts = ts

        total_inserted += batch_inserted

        # 不足一批说明拉完了
        if len(messages) < batch_size:
            break

        offset += batch_size

    if last_ts:
        await db.execute(
            """INSERT OR REPLACE INTO sync_state (platform, chat_id, last_timestamp, updated_at)
               VALUES ('wechat', ?, ?, CURRENT_TIMESTAMP)""",
            (chat_id, last_ts),
        )

    return total_inserted


async def sync_new_messages() -> int:
    """Sync only new messages since last check using wx new-messages."""
    try:
        raw = await _run_wx("new-messages")
    except RuntimeError:
        return 0

    messages = json.loads(raw)
    if not messages:
        return 0

    ensure_dirs()
    inserted = 0

    async with aiosqlite.connect(str(DB_PATH), timeout=30) as db:
        for msg in messages:
            ts = _parse_ts(msg.get("timestamp") or msg.get("time") or msg.get("created_at"))
            chat_id = msg.get("username") or msg.get("chat_id") or ""
            chat_name = msg.get("chat") or msg.get("chat_name") or msg.get("name") or ""

            if not chat_id or not ts:
                continue

            content = msg.get("content") or msg.get("text") or ""
            cursor = await db.execute(
                """INSERT OR IGNORE INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "wechat",
                    chat_id,
                    chat_name,
                    "group" if chat_id.endswith("@chatroom") else "private",
                    msg.get("sender_id") or msg.get("talker") or "",
                    msg.get("sender") or msg.get("sender_name") or msg.get("nickname") or "",
                    content,
                    msg.get("type", "text"),
                    ts,
                    _source_id(msg, chat_id, ts, content),
                    json.dumps(msg, ensure_ascii=False),
                ),
            )
            inserted += max(cursor.rowcount, 0)

        await db.commit()

    return inserted
