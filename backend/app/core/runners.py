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
    """Post-ingest pipeline, run after anything that added messages (sync or
    file import): keyword triggers inline, then the derived-data task (kind
    backfill + stats rollups) in the background. Best-effort — a failure here
    must not surface as a sync failure since the data already landed."""
    try:
        from app.services.triggers import scan_for_matches
        await scan_for_matches()
    except Exception:
        pass
    try:
        from app.services.stats import spawn_refresh_if_needed
        spawn_refresh_if_needed()
    except Exception:
        pass


async def run_stats_refresh(task_id: str, *, full: bool = False):
    """Derived-data task: legacy kind backfill (no-op once caught up), then the
    stats rollups. One task type ('stats') so the UI shows a single progress."""
    from app.services.stats import backfill_kinds, refresh
    _tasks[task_id].update(status="running", progress=1, message="正在更新统计...")
    try:
        backfilled = await backfill_kinds(progress=make_progress(task_id))
        result = await refresh(progress=make_progress(task_id), full=full)
        msg = f"统计已更新（处理 {result['processed']:,} 条）"
        if backfilled["processed"]:
            msg += f"，类型归一 {backfilled['processed']:,} 条"
        _tasks[task_id].update(status="done", progress=100, message=msg, **result)
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


def sync_result_callback(task_id, platform, previous=None):
    from app.services.sync.result import merge_attempt
    def save(result):
        results = dict(_tasks[task_id].get("results", {}))
        results[platform] = json.loads(json.dumps(merge_attempt(previous, result) if previous else result))
        _tasks[task_id].update(results=results)
    return save


def finish_sync_task(task_id):
    from app.services.sync.result import result_status
    results = _tasks[task_id].get("results", {})
    states = [result_status(r) for r in results.values()]
    status = "done" if states and all(s == "done" for s in states) else "error" if states and all(s == "error" for s in states) else "partial"
    labels = {"wechat": "微信", "qq": "QQ", "telegram": "Telegram"}
    parts = []
    for platform, r in results.items():
        failed = sum(d["status"] == "failed" for d in r.get("details", []))
        pending = sum(d.get("count", 1) for d in r.get("details", []) if d["status"] == "pending")
        parts.append(f"{labels.get(platform, platform)} +{r['imported']}，成功 {r.get('chats', 0)}，失败 {failed}，待补 {pending}")
    label = {"done": "同步完成", "partial": "部分完成", "error": "同步失败"}[status]
    _tasks[task_id].update(status=status, progress=100, message=label + "：" + " | ".join(parts))


async def sync_platform(task_id, platform, *, new_only=False, mode=None, chat_ids=None, skip_ids=(), live_only=()):
    from app.services.sync.result import error_reason
    baseline = _tasks[task_id].get("results", {}).get(platform)
    callback = sync_result_callback(task_id, platform, baseline)
    progress = make_progress(task_id)
    try:
        if platform == "wechat":
            from app.services.sync.wechat import sync_new_messages, sync_sessions
            if new_only and mode is None:
                count = await sync_new_messages()
                result = {"platform": platform, "imported": count, "chats": 0, "details": [], "targets": 0}
            else:
                result = await sync_sessions(mode=mode or "incremental", progress=progress,
                                             on_result=callback, chat_ids=chat_ids, skip_ids=skip_ids, live_only=live_only,
                                             expected_cache=(baseline or {}).get("cache_targets", ()))
        elif platform == "qq":
            options = dict(progress=progress, on_result=callback, chat_ids=chat_ids, skip_ids=skip_ids)
            cfg = load_config()
            if cfg.qq.source == "local":
                from app.services.sync.qq_local import sync_local
                result = await sync_local(cfg, mode=mode, **options)
            else:
                from app.services.sync.qq_qce import sync_all
                result = await sync_all(**options)
        else:
            from app.services.sync.telegram_live import sync_all
            result = await sync_all(progress=progress, on_result=callback, chat_ids=chat_ids, skip_ids=skip_ids)
        result["finished"] = True
        callback(result)
    except Exception as e:
        previous = _tasks[task_id].get("results", {}).get(platform, {})
        result = {**previous, "platform": platform, "targets": previous.get("targets", 1),
                  "chats": previous.get("chats", 0), "imported": previous.get("imported", 0), "finished": False,
                  "details": [d for d in previous.get("details", []) if d["chat_id"] != "@platform"] +
                             [{"chat_id": "@platform", "name": platform, "status": "failed",
                               "error": error_reason(e), "imported": 0}]}
        sync_result_callback(task_id, platform)(result)


async def run_sync(task_id: str, new_only: bool = False, *, mode=None, chat_ids=None):
    await run_platforms_sync(task_id, ["wechat"], new_only=new_only, mode=mode, selections={"wechat": chat_ids})


async def run_platforms_sync(task_id, platforms, *, new_only=False, mode=None, selections=None, skips=None, live_only=()):
    from app.services.backup import maybe_backup_before_sync
    if not _tasks[task_id].get("request"):
        _tasks[task_id].update(request={"platforms": platforms, "new_only": new_only, "mode": mode,
                                        "selections": selections})
    _tasks[task_id].update(status="running", progress=1, message="准备同步...")
    await maybe_backup_before_sync()
    for platform in platforms:
        _tasks[task_id].update(message=f"正在同步 {platform}...")
        await sync_platform(task_id, platform, new_only=new_only, mode=mode,
                            chat_ids=(selections or {}).get(platform),
                            skip_ids=(skips or {}).get(platform, ()), live_only=live_only)
    await _post_sync_scan()
    finish_sync_task(task_id)


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

        await _post_sync_scan()
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"导入完成: {result['imported']} 条消息 ({result.get('chats', 1)} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def run_qq_sync(task_id: str, *, mode=None, chat_ids=None):
    await run_platforms_sync(task_id, ["qq"], mode=mode, selections={"qq": chat_ids})


async def run_telegram_sync(task_id: str):
    await run_platforms_sync(task_id, ["telegram"])


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


async def run_report(task_id: str, period: str, notify: bool = False, year: int | None = None):
    from app.services.digest import PERIODS, generate_digest
    label = f"{year} 年报" if (period == "yearly" and year) else PERIODS[period][1]
    _tasks[task_id].update(status="running", progress=5, message=f"正在生成{label}...")
    try:
        result = await generate_digest(period, progress=make_progress(task_id), task_id=task_id, year=year)
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"{label}已生成（{result['range']}）", report_id=result["id"],
        )
        if notify:
            from app.services.triggers import _osascript_notify
            await _osascript_notify(f"{label}已生成", "到「报告」页查看")
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
    ("id", "platform", "chat_id", "chat_name", "sender_id", "sender_name", "content", "msg_type", "kind", "timestamp")
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
    else:
        clause, clause_params = chat_filter_clause(cfg.chat_filter, alias="m")
        if clause:
            conditions.append(clause)
            params.extend(clause_params)
    return conditions, params


def chat_filter_clause(cf, *, alias: str = "m") -> tuple[str, list]:
    """SQL for the analysis scope. Keyed on (platform, chat_id) via `refs`;
    legacy display names in `chats` are still honoured until the startup
    migration has resolved them all."""
    parts: list[str] = []
    params: list = []
    if cf.refs:
        pairs = " OR ".join(f"({alias}.platform = ? AND {alias}.chat_id = ?)" for _ in cf.refs)
        parts.append(f"({pairs})")
        for r in cf.refs:
            params.extend([r.platform, r.chat_id])
    if cf.chats:
        ph = ",".join("?" * len(cf.chats))
        parts.append(f"{alias}.chat_name IN ({ph})")
        params.extend(cf.chats)
    if not parts:
        return "", []
    inner = " OR ".join(parts)
    return (f"({inner})" if cf.mode == "whitelist" else f"NOT ({inner})"), params


async def _select_unanalyzed(db, conditions: list[str], params: list, limit: int) -> list[dict]:
    """Incremental selection against analysis_state checkpoints.

    Chats with a checkpoint drain their backlog oldest-first, so a burst larger
    than `limit` is worked off across runs instead of being skipped. Chats never
    analyzed before are seeded with their newest messages (history older than
    the seed is reachable via full=true or since=...)."""
    scope_where = (" AND " + " AND ".join(conditions)) if conditions else ""

    # (timestamp, id) composite cursor: strict on timestamp, then break ties by
    # id so a burst of same-second messages split across runs is never skipped.
    backlog = await db.execute_fetchall(
        f"""SELECT {_MSG_COLUMNS} FROM messages m
            JOIN analysis_state s ON s.platform = m.platform AND s.chat_id = m.chat_id
            WHERE s.last_analyzed_ts IS NOT NULL
              AND (m.timestamp > s.last_analyzed_ts
                   OR (s.last_analyzed_id IS NOT NULL
                       AND m.timestamp = s.last_analyzed_ts AND m.id > s.last_analyzed_id))
              {scope_where}
            ORDER BY m.timestamp ASC, m.id ASC LIMIT ?""",
        [*params, limit],
    )
    rows = [dict(r) for r in backlog]

    remaining = limit - len(rows)
    if remaining > 0:
        # Tie-break by id so the "newest N" cut is deterministic and the seeded
        # set aligns with the (timestamp, id) checkpoint written afterwards.
        seed = await db.execute_fetchall(
            f"""SELECT {_MSG_COLUMNS} FROM messages m
                LEFT JOIN analysis_state s ON s.platform = m.platform AND s.chat_id = m.chat_id
                WHERE s.chat_id IS NULL{scope_where}
                ORDER BY m.timestamp DESC, m.id DESC LIMIT ?""",
            [*params, remaining],
        )
        rows.extend(dict(r) for r in reversed(seed))

    rows.sort(key=lambda m: (m["timestamp"], m["id"]))
    return rows


async def _advance_analysis_state(messages: list[dict]) -> None:
    """Advance per-chat checkpoints to the furthest processed (timestamp, id).
    A read-compare-write guard skips rewinds so a manual since=/full run over
    old history can't drag the incremental cursor backwards. Analyze is
    single-flight (is_task_running), so there's no concurrent writer to race."""
    latest: dict[tuple[str, str], tuple[str, int]] = {}
    for m in messages:
        key = (m.get("platform") or "", m["chat_id"])
        cur = (m["timestamp"], int(m["id"]))
        if key not in latest or cur > latest[key]:
            latest[key] = cur

    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        db.row_factory = aiosqlite.Row
        for (platform, chat_id), (ts, mid) in latest.items():
            existing = await db.execute_fetchall(
                "SELECT last_analyzed_ts, last_analyzed_id FROM analysis_state WHERE platform=? AND chat_id=?",
                (platform, chat_id),
            )
            if existing:
                old = (existing[0]["last_analyzed_ts"] or "", existing[0]["last_analyzed_id"] or 0)
                if (ts, mid) <= old:
                    continue  # never rewind
            await db.execute(
                """INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts, last_analyzed_id, updated_at)
                   VALUES (?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(platform, chat_id) DO UPDATE SET
                       last_analyzed_ts = excluded.last_analyzed_ts,
                       last_analyzed_id = excluded.last_analyzed_id,
                       updated_at = CURRENT_TIMESTAMP""",
                (platform, chat_id, ts, mid),
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

            from app.services.persons import vip_identity_values
            vip_extra = await vip_identity_values(db, cfg.vip_contacts)

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
