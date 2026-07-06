"""Long-running task implementations. Used by both API routers and the scheduler."""
import json
from pathlib import Path

import aiosqlite

from app.core import database
from app.core.config import load_config
from app.core.tasks import _tasks, make_progress
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
        result = await embed_all_knowledge(progress=make_progress(task_id), task_id=task_id)
        _tasks[task_id].update(
            status="done", progress=100,
            message=(f"embed 完成: {result['embedded']} 条" if result['total']
                     else "无待 embed 的知识点"),
            **result,
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


def spawn_embed_if_needed() -> None:
    """Auto-embed hook: fired after knowledge writes so semantic search always
    covers the newest items. No-op without an embedding model or if an embed
    task is already running."""
    from app.core.tasks import create_task, is_task_running, spawn_cancellable
    if not load_config().llm.embedding_model or is_task_running("embed"):
        return
    task_id = create_task("embed")
    spawn_cancellable(task_id, run_embed_knowledge(task_id))


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
    svc.analyzer.task_id = task_id
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


_MSG_COLUMNS = ", ".join(
    f"m.{c}" for c in
    ("id", "platform", "chat_id", "chat_name", "sender_id", "sender_name", "content", "msg_type", "timestamp")
)


def _scope_conditions(req: AnalyzeRequest, cfg) -> tuple[list[str], list]:
    """Scope filters, qualified with the `m` alias so they compose with joins."""
    conditions: list[str] = []
    params: list = []
    chat_names = req.chats or ([req.chat] if req.chat else None)

    if req.chat_id:
        conditions.append("m.chat_id = ?")
        params.append(req.chat_id)
        if req.platform:
            conditions.append("m.platform = ?")
            params.append(req.platform)
    elif chat_names:
        placeholders = ",".join("?" * len(chat_names))
        conditions.append(f"m.chat_name IN ({placeholders})")
        params.extend(chat_names)
    elif cfg.chat_filter.chats:
        placeholders = ",".join("?" * len(cfg.chat_filter.chats))
        if cfg.chat_filter.mode == "whitelist":
            conditions.append(f"m.chat_name IN ({placeholders})")
        else:
            conditions.append(f"m.chat_name NOT IN ({placeholders})")
        params.extend(cfg.chat_filter.chats)
    return conditions, params


async def _select_unanalyzed(db, conditions: list[str], params: list, limit: int) -> list[dict]:
    """Incremental selection against analysis_state checkpoints.

    Chats with a checkpoint drain their backlog oldest-first, so a burst larger
    than `limit` is worked off across runs instead of being skipped. Chats never
    analyzed before are seeded with their newest messages (history older than
    the seed is reachable via full=true or since=...)."""
    scope_where = (" AND " + " AND ".join(conditions)) if conditions else ""

    backlog = await db.execute_fetchall(
        f"""SELECT {_MSG_COLUMNS} FROM messages m
            JOIN analysis_state s ON s.platform = m.platform AND s.chat_id = m.chat_id
            WHERE s.last_analyzed_ts IS NOT NULL AND m.timestamp > s.last_analyzed_ts{scope_where}
            ORDER BY m.timestamp ASC LIMIT ?""",
        [*params, limit],
    )
    rows = [dict(r) for r in backlog]

    remaining = limit - len(rows)
    if remaining > 0:
        seed = await db.execute_fetchall(
            f"""SELECT {_MSG_COLUMNS} FROM messages m
                LEFT JOIN analysis_state s ON s.platform = m.platform AND s.chat_id = m.chat_id
                WHERE s.chat_id IS NULL{scope_where}
                ORDER BY m.timestamp DESC LIMIT ?""",
            [*params, remaining],
        )
        rows.extend(dict(r) for r in reversed(seed))

    rows.sort(key=lambda m: m["timestamp"])
    return rows


async def _advance_analysis_state(messages: list[dict]) -> None:
    """Advance per-chat checkpoints to the newest processed timestamp. MAX()
    guards against rewinding when a manual since=... run touches old history."""
    latest: dict[tuple[str, str], str] = {}
    for m in messages:
        key = (m.get("platform") or "", m["chat_id"])
        ts = m["timestamp"]
        if key not in latest or ts > latest[key]:
            latest[key] = ts

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        for (platform, chat_id), ts in latest.items():
            await db.execute(
                """INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts, updated_at)
                   VALUES (?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(platform, chat_id) DO UPDATE SET
                       last_analyzed_ts = MAX(COALESCE(analysis_state.last_analyzed_ts, ''), excluded.last_analyzed_ts),
                       updated_at = CURRENT_TIMESTAMP""",
                (platform, chat_id, ts),
            )
        await db.commit()


async def _persist_batch(task_id: str, items: list[dict], summary: str) -> None:
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute(
            "INSERT OR REPLACE INTO analysis_batches (task_id, summary, status) VALUES (?,?, 'pending')",
            (task_id, summary),
        )
        for item in items:
            await db.execute(
                """INSERT INTO pending_items
                   (task_id, title, content, category, urgency, tags, source_chat, source_message_ids, batch_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    str(item.get("title", "")),
                    str(item.get("content", "")),
                    str(item.get("category") or ""),
                    int(item.get("urgency") or 3),
                    json.dumps(item.get("tags", []), ensure_ascii=False),
                    item.get("source_chat", ""),
                    item.get("source_message_ids", "[]"),
                    item.get("batch_id", ""),
                ),
            )
        await db.commit()


async def run_analyze(task_id: str, req: AnalyzeRequest):
    from app.services.analyzer import AnalyzerService

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在分析消息..."
    _tasks[task_id]["progress"] = 10

    try:
        svc = AnalyzerService()
        svc.task_id = task_id
        cfg = load_config()

        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            db.row_factory = aiosqlite.Row

            conditions, params = _scope_conditions(req, cfg)
            if req.since:
                add_time_filters(conditions, params, "m.timestamp", req.since, None)
            add_time_filters(conditions, params, "m.timestamp", None, req.until)

            if req.since or req.full:
                where = " AND ".join(conditions)
                rows = await db.execute_fetchall(
                    f"""SELECT * FROM (
                            SELECT {_MSG_COLUMNS} FROM messages m {'WHERE ' + where if where else ''}
                            ORDER BY m.timestamp DESC LIMIT ?
                        ) ORDER BY timestamp ASC""",
                    [*params, req.limit],
                )
                messages = [dict(r) for r in rows]
            else:
                messages = await _select_unanalyzed(db, conditions, params, req.limit)

            rejected = await db.execute_fetchall(
                "SELECT DISTINCT title FROM pending_items WHERE status='rejected' ORDER BY id DESC LIMIT 8"
            )
            svc.rejected_examples = [r["title"] for r in rejected]

            vip_extra: set[str] = set()
            if cfg.vip_contacts:
                placeholders = ",".join("?" * len(cfg.vip_contacts))
                alias_rows = await db.execute_fetchall(
                    f"SELECT platform_id, platform_name FROM contact_aliases WHERE canonical_name IN ({placeholders})",
                    cfg.vip_contacts,
                )
                vip_extra = {v for row in alias_rows for v in (row["platform_id"], row["platform_name"]) if v}

        _tasks[task_id]["progress"] = 30
        _tasks[task_id]["message"] = f"找到 {len(messages)} 条消息，开始分析..."

        def on_progress(pct, msg):
            _tasks[task_id].update(progress=pct, message=msg)

        knowledge_items, summary, completed = await svc.analyze_messages(
            messages, on_progress=on_progress, vip_extra=vip_extra,
        )

        all_failed = bool(svc.llm_call_count) and svc.llm_fail_count == svc.llm_call_count
        await _persist_batch(task_id, knowledge_items, summary)
        # Advance checkpoints only over fully-processed input: a budget break or
        # total LLM failure must leave unprocessed messages reachable next run.
        if completed and messages and not all_failed:
            await _advance_analysis_state(messages)

        if not knowledge_items and all_failed:
            _tasks[task_id].update(
                status="error", progress=0,
                message=f"LLM 调用全部失败：{svc.last_llm_error or '未知错误'}",
            )
            return

        msg = f"提取到 {len(knowledge_items)} 个知识点，请筛选后保存"
        if not completed:
            msg += "（今日预算已用尽，本次未分析完，游标未推进）"
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
