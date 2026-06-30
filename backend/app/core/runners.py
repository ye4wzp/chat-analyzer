"""Long-running task implementations. Used by both API routers and the scheduler."""
from pathlib import Path

import aiosqlite

from app.core import database
from app.core.config import load_config
from app.core.tasks import _pending_results, _tasks, make_progress
from app.core.time_utils import add_time_filters
from app.models.analyze import AnalyzeRequest


async def _post_sync_scan() -> None:
    """Run keyword trigger scan after a successful sync. Best-effort — any
    failure here must not surface as a sync failure since data already landed."""
    try:
        from app.services.triggers import scan_for_matches
        await scan_for_matches()
    except Exception:
        pass


async def run_sync(task_id: str, new_only: bool):
    from app.services.sync.wechat import sync_new_messages, sync_sessions
    from app.services.backup import maybe_backup_before_sync

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步微信数据..."
    _tasks[task_id]["progress"] = 30

    try:
        await maybe_backup_before_sync()  # throttled 5min, swallows failures
        count = await (sync_new_messages() if new_only else sync_sessions())
        await _post_sync_scan()
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
    from app.services.backup import maybe_backup_before_sync
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 QQ..."
    _tasks[task_id]["progress"] = 5
    try:
        await maybe_backup_before_sync()
        result = await sync_all(progress=make_progress(task_id))
        await _post_sync_scan()
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_telegram_sync(task_id: str):
    from app.services.sync.telegram_live import sync_all
    from app.services.backup import maybe_backup_before_sync
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 Telegram..."
    _tasks[task_id]["progress"] = 5
    try:
        await maybe_backup_before_sync()
        result = await sync_all(progress=make_progress(task_id))
        await _post_sync_scan()
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个对话)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_embed_knowledge(task_id: str):
    """Embed all knowledge items missing a vector. Pure index job — no DB writes
    outside the embedding column."""
    from app.services.embedder import embed_all_knowledge
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "准备 embedding..."
    _tasks[task_id]["progress"] = 0
    try:
        result = await embed_all_knowledge(progress=make_progress(task_id))
        _tasks[task_id].update(
            status="done", progress=100,
            message=(f"embed 完成: {result['embedded']} 条" if result['total']
                     else "无待 embed 的知识点"),
            **result,
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


async def run_tag_batch(
    task_id: str, include_groups: bool, only_untagged: bool, msg_limit: int, max_contacts: int | None
):
    """Tag contacts in bulk. For each contact: read its recent messages, ask the
    LLM for tags, persist them as suggestions. The DB connection is never held
    across an LLM call to avoid blocking concurrent syncs."""
    import uuid

    from app.services.tagger import TaggerService, persist_suggestions

    _tasks[task_id].update(status="running", progress=2, message="准备联系人列表...")
    svc = TaggerService()
    batch_id = uuid.uuid4().hex[:8]
    group_clause = "" if include_groups else " WHERE chat_id NOT LIKE '%@chatroom'"

    try:
        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            db.row_factory = aiosqlite.Row
            active_tags = [
                r["name"] for r in await db.execute_fetchall(
                    "SELECT name FROM contact_tags WHERE status='active' ORDER BY name"
                )
            ]
            contacts = [dict(r) for r in await db.execute_fetchall(
                f"""SELECT platform, chat_id, chat_name, COUNT(*) AS n
                    FROM messages{group_clause}
                    GROUP BY platform, chat_id ORDER BY n DESC"""
            )]
            tagged = {
                (r["platform"], r["chat_id"]) for r in await db.execute_fetchall(
                    "SELECT DISTINCT platform, chat_id FROM contact_tag_links WHERE status='confirmed'"
                )
            }

        if only_untagged:
            contacts = [c for c in contacts if (c["platform"], c["chat_id"]) not in tagged]
        if max_contacts:
            contacts = contacts[:max_contacts]

        total = len(contacts)
        if total == 0:
            _tasks[task_id].update(status="done", progress=100, message="没有需要打标签的联系人")
            return

        tagged_count = 0
        for i, c in enumerate(contacts):
            _tasks[task_id].update(
                progress=5 + int(90 * i / total),
                message=f"打标签 {i + 1}/{total}：{c['chat_name'] or c['chat_id']}",
            )
            async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
                db.row_factory = aiosqlite.Row
                rows = await db.execute_fetchall(
                    """SELECT id, sender_name, content, timestamp FROM messages
                       WHERE platform=? AND chat_id=? ORDER BY timestamp DESC LIMIT ?""",
                    (c["platform"], c["chat_id"], msg_limit),
                )
            msgs = [dict(r) for r in reversed(rows)]

            suggestions = await svc.suggest_tags(c["chat_name"] or "", msgs, active_tags)
            if not suggestions:
                continue
            async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
                await db.execute("PRAGMA busy_timeout=30000")
                await persist_suggestions(db, c["platform"], c["chat_id"], suggestions, batch_id)
                await db.commit()
            tagged_count += 1

        msg = f"完成：{tagged_count}/{total} 位联系人有标签建议，待审核"
        if svc.analyzer.llm_fail_count:
            msg += f"（{svc.analyzer.llm_fail_count}/{svc.analyzer.llm_call_count} 次 LLM 失败：{svc.last_llm_error}）"
        _tasks[task_id].update(status="done", progress=100, message=msg, batch_id=batch_id)
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_analyze(task_id: str, req: AnalyzeRequest):
    from app.services.analyzer import AnalyzerService

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在分析消息..."
    _tasks[task_id]["progress"] = 10

    try:
        svc = AnalyzerService()
        cfg = load_config()

        chat_names = req.chats or ([req.chat] if req.chat else None)

        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            db.row_factory = aiosqlite.Row

            conditions: list[str] = []
            params: list = []

            if req.chat_id:
                conditions.append("chat_id = ?")
                params.append(req.chat_id)
                if req.platform:
                    conditions.append("platform = ?")
                    params.append(req.platform)
            elif chat_names:
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
                add_time_filters(conditions, params, "timestamp", req.since, None)
            elif not req.full:
                # Incremental: only analyze messages after the latest source
                # message already saved for the current chat/platform scope.
                scope_where = (" AND " + " AND ".join(conditions)) if conditions else ""
                last_msg = await db.execute_fetchall(
                    f"""SELECT MAX(m.timestamp) as last_ts FROM messages m
                       JOIN knowledge_items k ON (
                           ',' || REPLACE(REPLACE(REPLACE(k.source_message_ids, ' ', ''), '[', ''), ']', '') || ','
                       ) LIKE ('%,' || m.id || ',%')
                       WHERE 1=1{scope_where}""",
                    params,
                )
                since_ts = last_msg[0]["last_ts"] if last_msg and last_msg[0]["last_ts"] else None
                if since_ts:
                    conditions.append("timestamp > ?")
                    params.append(since_ts)

            add_time_filters(conditions, params, "timestamp", None, req.until)

            where = " AND ".join(conditions)
            sql = f"""SELECT id, chat_id, chat_name, sender_name, content, msg_type, timestamp
                      FROM (
                          SELECT id, chat_id, chat_name, sender_name, content, msg_type, timestamp
                          FROM messages {'WHERE ' + where if where else ''}
                          ORDER BY timestamp DESC LIMIT ?
                      )
                      ORDER BY timestamp ASC"""
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
