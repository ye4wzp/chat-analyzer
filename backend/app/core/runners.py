"""Long-running task implementations. Used by both API routers and the scheduler."""
from pathlib import Path

import aiosqlite

from app.core import database
from app.core.config import load_config
from app.core.tasks import _pending_results, _tasks, make_progress
from app.models.analyze import AnalyzeRequest


async def run_sync(task_id: str, new_only: bool):
    from app.services.sync.wechat import sync_new_messages, sync_sessions

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步微信数据..."
    _tasks[task_id]["progress"] = 30

    try:
        count = await (sync_new_messages() if new_only else sync_sessions())
        _tasks[task_id].update(status="done", progress=100, message=f"同步完成，新增 {count} 条消息")
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_import(task_id: str, platform: str, path: str):
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = f"正在导入 {platform} 数据..."
    _tasks[task_id]["progress"] = 30

    try:
        if platform == "qq":
            from app.services.sync.qq import import_qq_dir, import_qq_json
            result = await (import_qq_dir(path) if Path(path).is_dir() else import_qq_json(path))
        else:
            from app.services.sync.telegram import import_telegram_dir, import_telegram_json
            result = await (import_telegram_dir(path) if Path(path).is_dir() else import_telegram_json(path))

        _tasks[task_id].update(
            status="done", progress=100,
            message=f"导入完成: {result['imported']} 条消息 ({result.get('chats', 1)} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_qq_sync(task_id: str):
    from app.services.sync.qq_qce import sync_all
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 QQ..."
    _tasks[task_id]["progress"] = 5
    try:
        result = await sync_all(progress=make_progress(task_id))
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_telegram_sync(task_id: str):
    from app.services.sync.telegram_live import sync_all
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 Telegram..."
    _tasks[task_id]["progress"] = 5
    try:
        result = await sync_all(progress=make_progress(task_id))
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个对话)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_qq_install(task_id: str, force: bool):
    from app.services.sync.qq_launcher import LauncherError, ensure_installed
    _tasks[task_id].update(status="running", progress=5, message="拉取 QCE release 信息...")
    try:
        result = await ensure_installed(force=force)
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"已安装 {result['version']}" + (" (已是最新)" if result["already_installed"] else ""),
            **result,
        )
    except LauncherError as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=f"安装失败: {e}")


async def run_analyze(task_id: str, req: AnalyzeRequest):
    from app.services.analyzer import AnalyzerService

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在分析消息..."
    _tasks[task_id]["progress"] = 10

    try:
        svc = AnalyzerService()
        cfg = load_config()

        chat_names = req.chats or ([req.chat] if req.chat else None)

        async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
            db.row_factory = aiosqlite.Row

            conditions: list[str] = []
            params: list = []

            if chat_names:
                placeholders = ",".join("?" * len(chat_names))
                conditions.append(f"chat_name IN ({placeholders})")
                params.extend(chat_names)
            elif cfg.chat_filter.chats:
                placeholders = ",".join("?" * len(cfg.chat_filter.chats))
                if cfg.chat_filter.mode == "whitelist":
                    conditions.append(f"chat_name IN ({placeholders})")
                else:
                    conditions.append(f"chat_name NOT IN ({placeholders})")
                params.extend(cfg.chat_filter.chats)

            if req.since:
                conditions.append("timestamp >= ?")
                params.append(req.since)
            elif not req.full:
                # incremental: only analyze messages after last analyzed timestamp
                last = await db.execute_fetchall(
                    "SELECT MAX(created_at) as last FROM knowledge_items"
                )
                last_ts = last[0]["last"] if last and last[0]["last"] else None
                if last_ts:
                    last_msg = await db.execute_fetchall(
                        """SELECT MAX(m.timestamp) as last_ts FROM messages m
                           JOIN knowledge_items k ON (
                               k.source_message_ids = ('[' || m.id || ']')
                               OR k.source_message_ids LIKE ('[' || m.id || ',%')
                               OR k.source_message_ids LIKE ('%,' || m.id || ']')
                               OR k.source_message_ids LIKE ('%,' || m.id || ',%')
                           )
                           WHERE k.created_at >= ?""",
                        (last_ts,),
                    )
                    since_ts = last_msg[0]["last_ts"] if last_msg and last_msg[0]["last_ts"] else None
                    if since_ts:
                        conditions.append("timestamp > ?")
                        params.append(since_ts)

            if req.until:
                conditions.append("timestamp <= ?")
                params.append(req.until)

            where = " AND ".join(conditions)
            sql = f"""SELECT id, chat_id, chat_name, sender_name, content, msg_type, timestamp
                      FROM messages {'WHERE ' + where if where else ''}
                      ORDER BY timestamp DESC LIMIT ?"""
            params.append(req.limit)
            rows = await db.execute_fetchall(sql, params)

        _tasks[task_id]["progress"] = 30
        _tasks[task_id]["message"] = f"找到 {len(rows)} 条消息，开始分析..."

        messages = [dict(r) for r in rows]

        def on_progress(pct, msg):
            _tasks[task_id].update(progress=pct, message=msg)

        knowledge_items, summary = await svc.analyze_messages(messages, on_progress=on_progress)
        _pending_results[task_id] = knowledge_items

        # If 0 items came back AND all LLM calls failed, surface the LLM error
        # rather than the misleading "提取到 0 个知识点".
        if not knowledge_items and svc.llm_fail_count and svc.llm_fail_count == svc.llm_call_count:
            _tasks[task_id].update(
                status="error", progress=0,
                message=f"LLM 调用全部失败：{svc.last_llm_error or '未知错误'}",
            )
            return

        msg = f"提取到 {len(knowledge_items)} 个知识点，请筛选后保存"
        if svc.llm_fail_count:
            msg += f"（{svc.llm_fail_count}/{svc.llm_call_count} 次 LLM 调用失败：{svc.last_llm_error}）"
        _tasks[task_id].update(
            status="done", progress=100,
            message=msg,
            result_count=len(knowledge_items),
            summary=summary,
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))
