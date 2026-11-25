"""Storage maintenance: explicit, user-triggered, irreversible operations.

Nothing here runs automatically. Each action states what it removes, the
frontend confirms, and the work runs as a task so progress is visible."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import database
from app.core.config import ChatRef, load_config, save_config
from app.core.tasks import _tasks, create_task, is_task_running, spawn_cancellable

router = APIRouter()


class PurgeChatsRequest(BaseModel):
    chat_type: Optional[str] = None      # e.g. 'channel' — every chat of that type
    refs: list[ChatRef] = []             # and/or specific chats
    exclude_from_sync: bool = True       # also add to sync_scope so they don't come back


@router.get("/api/maintenance/storage")
async def storage_report():
    """What takes space, so the user can decide what to drop."""
    async with database.connect_db() as db:
        size = await db.execute_fetchall("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()")
        free = await db.execute_fetchall("SELECT freelist_count * page_size FROM pragma_freelist_count(), pragma_page_size()")
        raw = await db.execute_fetchall(
            """SELECT platform, COUNT(*) AS n, COALESCE(SUM(LENGTH(raw_data)), 0) AS bytes
               FROM messages WHERE raw_data IS NOT NULL AND kind = 'text' AND platform IN ('wechat', 'telegram')
               GROUP BY platform"""
        )
        by_type = await db.execute_fetchall(
            """SELECT chat_type, COUNT(*) AS chats, SUM(n) AS messages FROM stats_chats GROUP BY chat_type"""
        )
        tables = await db.execute_fetchall(
            """SELECT name, SUM(pgsize) AS bytes FROM dbstat
               WHERE name IN ('messages', 'resources', 'stats_daily', 'stats_sender', 'stats_sender_hourly', 'messages_fts_data')
                  OR name LIKE 'idx_%' OR name LIKE 'sqlite_autoindex_resources%'
               GROUP BY name ORDER BY bytes DESC"""
        )
    return {
        "db_bytes": int(size[0][0]) if size else 0,
        "free_bytes": int(free[0][0]) if free else 0,
        "raw_text": [{"platform": r["platform"], "messages": int(r["n"]), "bytes": int(r["bytes"])} for r in raw],
        "by_chat_type": [{"chat_type": r["chat_type"], "chats": int(r["chats"]), "messages": int(r["messages"])} for r in by_type],
        "tables": [{"name": r["name"], "bytes": int(r["bytes"])} for r in tables],
        "keep_raw_text": load_config().storage.keep_raw_text,
        "running": is_task_running("maintenance"),
    }


async def _run_purge_raw_text(task_id: str):
    _tasks[task_id].update(status="running", progress=2, message="清理文本消息的原始 JSON...")
    try:
        cleared = 0
        async with database.connect_db() as db:
            await db.execute("PRAGMA busy_timeout=60000")
            while True:
                cur = await db.execute(
                    """UPDATE messages SET raw_data = NULL WHERE id IN (
                           SELECT id FROM messages
                           WHERE raw_data IS NOT NULL AND kind = 'text' AND platform IN ('wechat', 'telegram')
                           LIMIT 20000)"""
                )
                await db.commit()
                if cur.rowcount <= 0:
                    break
                cleared += cur.rowcount
                _tasks[task_id].update(progress=min(95, 2 + cleared // 20000), message=f"已清理 {cleared:,} 条...")
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"已清理 {cleared:,} 条文本消息的原始 JSON。空间会被后续写入复用；要立刻回收请执行「压缩数据库」。",
            cleared=cleared,
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def _run_purge_chats(task_id: str, body: PurgeChatsRequest):
    from app.services.stats import spawn_refresh_if_needed
    _tasks[task_id].update(status="running", progress=2, message="删除指定聊天的消息...")
    try:
        async with database.connect_db() as db:
            await db.execute("PRAGMA busy_timeout=60000")
            targets: list[tuple[str, str]] = [(r.platform, r.chat_id) for r in body.refs]
            if body.chat_type:
                rows = await db.execute_fetchall(
                    "SELECT DISTINCT platform, chat_id FROM stats_chats WHERE chat_type = ?", (body.chat_type,),
                )
                targets += [(r[0], r[1]) for r in rows]
            targets = list(dict.fromkeys(targets))
            deleted = 0
            for i, (platform, chat_id) in enumerate(targets):
                cur = await db.execute("DELETE FROM messages WHERE platform = ? AND chat_id = ?", (platform, chat_id))
                deleted += max(cur.rowcount, 0)
                if platform == "wechat" and chat_id.startswith("gh_"):
                    await db.execute("DELETE FROM wechat_archive_items WHERE chat_id=?", (chat_id,))
                    await db.execute("DELETE FROM wechat_archive_accounts WHERE chat_id=?", (chat_id,))
                await db.execute("DELETE FROM sync_state WHERE platform = ? AND chat_id = ?", (platform, chat_id))
                if platform == "qq":
                    await db.execute("DELETE FROM qq_local_cursor WHERE chat_id = ?", (chat_id,))
                await db.execute("DELETE FROM analysis_state WHERE platform = ? AND chat_id = ?", (platform, chat_id))
                await db.commit()
                _tasks[task_id].update(progress=2 + int(90 * (i + 1) / max(1, len(targets))), message=f"已删除 {deleted:,} 条（{i + 1}/{len(targets)} 个聊天）")
        if body.exclude_from_sync and targets:
            cfg = load_config()
            have = {r.key() for r in cfg.sync_scope.exclude_chats}
            for platform, chat_id in targets:
                if (platform, chat_id) not in have:
                    cfg.sync_scope.exclude_chats.append(ChatRef(platform=platform, chat_id=chat_id))
            if body.chat_type == "channel":
                cfg.sync_scope.exclude_channels = True
            save_config(cfg)
        spawn_refresh_if_needed(full=True)
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"已删除 {len(targets)} 个聊天的 {deleted:,} 条消息，统计重建中。要回收磁盘空间请执行「压缩数据库」。",
            deleted=deleted, chats=len(targets),
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def _run_vacuum(task_id: str):
    _tasks[task_id].update(status="running", progress=5, message="VACUUM 中（几 GB 的库需要几分钟，期间写入会等待）...")
    try:
        async with database.connect_db() as db:
            await db.execute("VACUUM")
        _tasks[task_id].update(status="done", progress=100, message="数据库已压缩")
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


def _start(coro) -> dict:
    if is_task_running("maintenance") or is_task_running("stats"):
        raise HTTPException(409, "已有维护/统计任务在运行")
    task_id = create_task("maintenance")
    spawn_cancellable(task_id, coro(task_id))
    return {"task_id": task_id}


@router.post("/api/maintenance/purge-raw-text")
async def purge_raw_text():
    return _start(_run_purge_raw_text)


@router.post("/api/maintenance/purge-chats")
async def purge_chats(body: PurgeChatsRequest):
    if not body.chat_type and not body.refs:
        raise HTTPException(400, "需要 chat_type 或 refs")
    return _start(lambda tid: _run_purge_chats(tid, body))


@router.post("/api/maintenance/vacuum")
async def vacuum():
    return _start(_run_vacuum)
