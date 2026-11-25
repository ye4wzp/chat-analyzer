import asyncio
import hashlib
import json
from datetime import datetime

import aiosqlite

from app.core.database import get_db, DB_PATH
from app.core.config import ensure_dirs, load_config
from app.core.time_utils import normalize_timestamp
from app.services.sync._common import in_sync_scope, upsert_message


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
        raise RuntimeError(f"wx {args[0]} 退出码 {proc.returncode}：{stderr.decode(errors='replace').strip()}")
    return stdout.decode()


def _rows(raw: str, key: str) -> list[dict]:
    """新版 wx-cli 把结果包成 {meta, <key>: [...]}；旧版直接返回数组。两种都吃。"""
    data = json.loads(raw)
    if isinstance(data, dict):
        data = data.get(key)
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise ValueError("InvalidWechatResponse")
    return data


def _parse_ts(ts_str: str | int | None) -> str | None:
    return normalize_timestamp(ts_str)


def _source_id(msg: dict, chat_id: str, ts: str, content: str) -> str:
    raw_id = msg.get("id") or msg.get("msg_id") or msg.get("msgid") or msg.get("local_id")
    if raw_id:
        return str(raw_id)
    sender = msg.get("sender_id") or msg.get("talker") or ""
    fingerprint = f"{chat_id}|{ts}|{sender}|{content}"
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


async def sync_sessions(*, mode="incremental", progress=None, on_result=None, chat_ids=None, skip_ids=(), live_only=(), expected_cache=()) -> dict:
    """Enumerate all sessions; full/repair also inspect the existing local cache."""
    from app.services.sync.result import new_result, record_result, record_missing, error_reason, WECHAT_FOLD_GROUP
    if mode not in ("incremental", "full", "repair"):
        raise ValueError("invalid sync mode")
    if chat_ids is not None:
        chat_ids = set(chat_ids) - {WECHAT_FOLD_GROUP} - set(skip_ids)
    limit = 1000
    while True:
        sessions = _rows(await _run_wx("sessions", "-n", str(limit)), "sessions")
        if len(sessions) < limit:
            break
        if limit >= 1_000_000:
            raise RuntimeError("微信会话列表可能被截断，未认定完整")
        limit *= 2
    result = new_result("wechat")
    cfg = load_config()
    selected = {}
    for session in sessions:
        cid = session.get("username") or session.get("chat_id") or ""
        if not cid or cid in skip_ids or (chat_ids is not None and cid not in chat_ids):
            continue
        if not in_sync_scope("wechat", cid, "group" if cid.endswith("@chatroom") else "private", cfg):
            result["excluded"] += 1
            continue
        selected[cid] = session.get("chat") or session.get("name") or session.get("nickname") or cid
    result["targets"] = len(selected)
    await record_missing(result, chat_ids, selected, on_result)
    missing_history = set()
    for i, (cid, name) in enumerate(selected.items()):
        if progress:
            progress(int(90 * i / max(1, len(selected))), f"微信 {i + 1}/{len(selected)}：{name}")
        if cid in live_only:
            await record_result(result, cid, name, "done", on_result=on_result)
            continue
        try:
            async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
                await db.execute("PRAGMA busy_timeout=60000")
                count = await _sync_chat_history(db, cid, name, full=mode != "incremental")
                await db.commit()
            await record_result(result, cid, name, "done", count, on_result=on_result)
        except Exception as e:
            reason = error_reason(e)
            if isinstance(e, RuntimeError) and "找不到" in reason and "的消息记录" in reason:
                missing_history.add(cid)
            await record_result(result, cid, name, "failed", error=reason, on_result=on_result)
    if mode in ("full", "repair"):
        from app.services.sync.wechat_cache import repair_cache
        await repair_cache(result, cfg, chat_ids=chat_ids, on_result=on_result, skip_ids=skip_ids, expected_cache=expected_cache)
    elif missing_history:
        # wx history can omit public-account shards. Recover only failed chats
        # from existing source caches, retaining the live-read failure reason.
        from app.services.sync.wechat_cache import repair_cache
        await repair_cache(result, cfg, chat_ids=missing_history, on_result=on_result, optional=True)
    return result


async def _sync_chat_history(
    db: aiosqlite.Connection, chat_id: str, chat_name: str, batch_size: int = 500, *, full: bool = False
) -> int:
    """Sync message history for a single chat with pagination. Returns new message count."""

    cursor = await db.execute(
        "SELECT last_timestamp FROM sync_state WHERE platform='wechat' AND chat_id=?",
        (chat_id,),
    )
    row = await cursor.fetchone()
    since_ts = row[0] if row and not full else None
    cfg = load_config()
    account = cfg.wechat.account.strip()

    legacy = set()
    if full:
        for (raw,) in await db.execute_fetchall(
            "SELECT raw_data FROM messages WHERE platform='wechat' AND chat_id=? AND source_id IS NULL AND raw_data IS NOT NULL", (chat_id,)
        ):
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("local_id") is not None:
                legacy.add(str(obj["local_id"]))
    total_inserted = 0
    offset = 0
    last_ts = since_ts

    while True:
        args = ["history", chat_id, "-n", str(batch_size), "--offset", str(offset)]
        if since_ts:
            args.extend(["--since", str(since_ts)[:10]])

        raw = await _run_wx(*args)

        messages = _rows(raw, "messages")
        if not messages:
            break

        batch_inserted = 0
        for msg in messages:
            ts = _parse_ts(msg.get("timestamp") or msg.get("time") or msg.get("created_at"))
            if not ts:
                raise ValueError("InvalidTimestamp")

            content = msg.get("content") or msg.get("text") or ""
            sid = _source_id(msg, chat_id, ts, content)
            if sid in legacy:
                continue
            batch_inserted += await upsert_message(
                db,
                platform="wechat",
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type="group" if chat_id.endswith("@chatroom") else "private",
                sender_id=msg.get("sender_id") or msg.get("talker") or "",
                sender_name=msg.get("sender") or msg.get("sender_name") or msg.get("nickname") or "",
                content=content,
                msg_type=msg.get("type", "text"),
                timestamp=ts,
                src_id=_source_id(msg, chat_id, ts, content),
                raw=msg,
                account=account,
                cfg=cfg,
            )
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
    raw = await _run_wx("new-messages")

    messages = _rows(raw, "messages")
    if not messages:
        return 0

    ensure_dirs()
    inserted = 0
    latest: dict[str, str] = {}
    cfg = load_config()
    account = cfg.wechat.account.strip()

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
                    if not in_sync_scope("wechat", chat_id, "group" if chat_id.endswith("@chatroom") else "private", cfg):
                        continue

                    content = msg.get("content") or msg.get("text") or ""
                    inserted += await upsert_message(
                        db,
                        platform="wechat",
                        chat_id=chat_id,
                        chat_name=chat_name,
                        chat_type="group" if chat_id.endswith("@chatroom") else "private",
                        sender_id=msg.get("sender_id") or msg.get("talker") or "",
                        sender_name=msg.get("sender") or msg.get("sender_name") or msg.get("nickname") or "",
                        content=content,
                        msg_type=msg.get("type", "text"),
                        timestamp=ts,
                        src_id=_source_id(msg, chat_id, ts, content),
                        raw=msg,
                        account=account,
                        cfg=cfg,
                    )
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
