import asyncio
import hashlib
import json
from datetime import datetime

import aiosqlite

from app.core.database import get_db, DB_PATH
from app.core.config import ensure_dirs
from app.core.time_utils import normalize_timestamp
from app.services.sync._common import current_account


WX_TIMEOUT = 300  # seconds; a hung wx-cli IPC must not pin the sync task forever


async def _run_wx(*args: str) -> str:
    """Run wx-cli command and return stdout."""
    proc = await asyncio.create_subprocess_exec(
        "wx", *args, "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=WX_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"wx {' '.join(args)} timed out after {WX_TIMEOUT}s")
    if proc.returncode != 0:
        raise RuntimeError(f"wx {' '.join(args)} failed: {stderr.decode()}")
    return stdout.decode()


def _rows(raw: str, key: str) -> list[dict]:
    """新版 wx-cli 把结果包成 {meta, <key>: [...]}；旧版直接返回数组。两种都吃。"""
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get(key)
    return data if isinstance(data, list) else []


def _parse_ts(ts_str: str | int | None) -> str | None:
    return normalize_timestamp(ts_str)


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
    sessions = _rows(raw, "sessions")
    if not sessions:
        return 0

    ensure_dirs()
    total = 0
    attempted = 0
    failed = 0
    last_error = ""

    for session in sessions:
        chat_name = session.get("chat") or session.get("name") or session.get("nickname") or ""
        chat_id = session.get("username") or session.get("chat_id") or ""

        if not chat_id:
            continue

        attempted += 1
        # Each chat gets its own short-lived connection to minimize lock hold time.
        # Retry on database-locked errors since parallel syncs may be writing.
        retries = 3
        for attempt in range(retries):
            try:
                async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=60000")
                    count = await _sync_chat_history(db, chat_id, chat_name)
                    await db.commit()
                    total += count
                break  # success
            except Exception as e:
                err_msg = str(e).lower()
                if "locked" in err_msg and attempt < retries - 1:
                    import logging
                    logging.warning("wechat sync locked for %s, retry %d/%d",
                                    chat_name or chat_id, attempt + 1, retries)
                    await asyncio.sleep(3 * (attempt + 1))
                    continue
                import logging
                logging.warning("wechat sync failed for %s: %s", chat_name or chat_id, e)
                failed += 1
                last_error = str(e)
                break

    # Every chat failing means a real fault (wx-cli output changed, IPC down) —
    # don't let it masquerade as "同步完成，新增 0 条消息".
    if attempted and failed == attempted:
        raise RuntimeError(f"微信同步失败：{attempted} 个会话全部出错，最后错误：{last_error}")

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
    account = current_account("wechat")

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

        messages = _rows(raw, "messages")
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
                    content, msg_type, timestamp, source_id, raw_data, account)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    account or None,
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
        # DO UPDATE (not REPLACE) so last_msg_id / backfill_page survive; only
        # advance the watermark, never rewind it.
        await db.execute(
            """INSERT INTO sync_state (platform, chat_id, last_timestamp, updated_at)
               VALUES ('wechat', ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(platform, chat_id) DO UPDATE SET
                 last_timestamp=MAX(excluded.last_timestamp,
                                    COALESCE(sync_state.last_timestamp, excluded.last_timestamp)),
                 updated_at=CURRENT_TIMESTAMP""",
            (chat_id, last_ts),
        )

    return total_inserted


async def sync_new_messages() -> int:
    """Sync only new messages since last check using wx new-messages."""
    try:
        raw = await _run_wx("new-messages")
    except RuntimeError:
        return 0

    messages = _rows(raw, "messages")
    if not messages:
        return 0

    ensure_dirs()
    inserted = 0
    latest: dict[str, str] = {}
    account = current_account("wechat")

    retries = 3
    for attempt in range(retries):
        try:
            async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=60000")
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
                            content, msg_type, timestamp, source_id, raw_data, account)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                            account or None,
                        ),
                    )
                    inserted += max(cursor.rowcount, 0)
                    if ts > latest.get(chat_id, ""):
                        latest[chat_id] = ts

                # 增量路径也要推进检查点，否则 sync/health 的同步时间永远停在上次全量同步。
                # 只向前推进：不把全量同步留下的更新的检查点改小。
                for cid, ts in latest.items():
                    await db.execute(
                        """INSERT INTO sync_state (platform, chat_id, last_timestamp, updated_at)
                           VALUES ('wechat', ?, ?, CURRENT_TIMESTAMP)
                           ON CONFLICT(platform, chat_id) DO UPDATE SET
                             last_timestamp=MAX(excluded.last_timestamp,
                                                COALESCE(sync_state.last_timestamp, excluded.last_timestamp)),
                             updated_at=CURRENT_TIMESTAMP""",
                        (cid, ts),
                    )

                await db.commit()
            break  # success
        except Exception as e:
            err_msg = str(e).lower()
            if "locked" in err_msg and attempt < retries - 1:
                import logging
                logging.warning("wechat sync_new locked, retry %d/%d", attempt + 1, retries)
                await asyncio.sleep(3 * (attempt + 1))
                inserted = 0  # reset count for retry
                latest.clear()
                continue
            raise

    return inserted
