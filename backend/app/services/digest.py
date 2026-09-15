"""Weekly/monthly digest: deterministic SQL stats + one LLM narrative pass,
persisted to the reports table. Fired by the scheduler or the Reports page.

Time bases differ by table: messages.timestamp is local-naive ISO (see
time_utils), while *_at audit columns are CURRENT_TIMESTAMP (UTC) — window
filters below match each accordingly.
"""
import logging
from datetime import datetime, timedelta

from app.core import database
from app.core.config import load_config

logger = logging.getLogger("app.digest")

PERIODS = {"weekly": (7, "周报", "周"), "monthly": (30, "月报", "月"), "yearly": (365, "年报", "年")}

NARRATIVE_PROMPT = """你是用户的个人聊天记录助理。根据以下统计数据和素材，写一份{label}正文。

要求：
- 用简洁的中文 Markdown，不要出现一级标题
- 结构：## 本{unit}概览（2-3 句话）、## 重点内容（基于知识点和对话摘要的要点列表）、## 值得关注（结合待办和联系人动态给 1-3 条建议）
- 只依据提供的素材归纳，不要编造具体事件

统计数据：
{stats}

本{unit}新增知识点标题：
{titles}

本{unit}对话分析摘要：
{summaries}"""


async def _window_stats(db, msg_since: str, audit_since_days: int, msg_until: str | None = None) -> dict:
    audit_since = f"datetime('now', '-{audit_since_days} days')"
    # Message counts come from the stats rollups (fast at millions of rows);
    # the day bound is inclusive so use the date part of both ends.
    day_lo = msg_since[:10]
    day_hi = (msg_until or "9999-12-31")[:10]
    platforms = await db.execute_fetchall(
        "SELECT platform, SUM(n) AS n FROM stats_daily WHERE day >= ? AND day <= ? GROUP BY platform",
        (day_lo, day_hi),
    )
    top_chats = await db.execute_fetchall(
        """SELECT COALESCE(c.chat_name, d.chat_id) AS name, SUM(d.n) AS n
           FROM stats_daily d
           LEFT JOIN stats_chats c ON c.account = d.account AND c.platform = d.platform AND c.chat_id = d.chat_id
           WHERE d.day >= ? AND d.day <= ? GROUP BY d.platform, d.chat_id ORDER BY n DESC LIMIT 5""",
        (day_lo, day_hi),
    )
    titles = await db.execute_fetchall(
        f"SELECT title FROM knowledge_items WHERE created_at >= {audit_since} ORDER BY id DESC LIMIT 20"
    )
    todo = await db.execute_fetchall(
        f"""SELECT
              SUM(CASE WHEN analyzed_at >= {audit_since} THEN 1 ELSE 0 END) AS new_todos,
              SUM(CASE WHEN done = 0 AND urgency >= 4 THEN 1 ELSE 0 END) AS open_urgent
            FROM analysis_results WHERE category = 'todo'"""
    )
    summaries = await db.execute_fetchall(
        f"""SELECT summary FROM analysis_batches
            WHERE created_at >= {audit_since} AND summary IS NOT NULL AND summary <> ''
            ORDER BY created_at DESC LIMIT 5"""
    )
    return {
        "platforms": [(r["platform"], int(r["n"])) for r in platforms],
        "top_chats": [(r["name"], int(r["n"])) for r in top_chats],
        "knowledge_titles": [r["title"] for r in titles],
        "new_todos": int(todo[0]["new_todos"] or 0),
        "open_urgent": int(todo[0]["open_urgent"] or 0),
        "summaries": [r["summary"] for r in summaries],
    }


async def _inactive_vips(db, msg_since: str) -> list[tuple[str, str | None]]:
    """VIPs (alias-resolved) with no message since the window start."""
    cfg = load_config()
    if not cfg.vip_contacts:
        return []
    inactive = []
    for name in cfg.vip_contacts:
        alias_rows = await db.execute_fetchall(
            "SELECT platform_id, platform_name FROM contact_aliases WHERE canonical_name = ?",
            (name,),
        )
        idents = {name} | {v for r in alias_rows for v in (r["platform_id"], r["platform_name"]) if v}
        placeholders = ",".join("?" * len(idents))
        params = [*idents, *idents, *idents]
        rows = await db.execute_fetchall(
            f"""SELECT MAX(timestamp) AS last_ts FROM messages
                WHERE sender_name IN ({placeholders}) OR sender_id IN ({placeholders})
                   OR chat_name IN ({placeholders})""",
            params,
        )
        last_ts = rows[0]["last_ts"]
        if last_ts is None or last_ts < msg_since:
            inactive.append((name, last_ts))
    return inactive


def _stats_lines(stats: dict, inactive: list[tuple[str, str | None]]) -> list[str]:
    total = sum(n for _, n in stats["platforms"])
    by_platform = "、".join(f"{p} {n}" for p, n in stats["platforms"]) or "无"
    lines = [
        f"- 消息总量：{total} 条（{by_platform}）",
        "- 最活跃会话：" + ("、".join(f"{name}（{n} 条）" for name, n in stats["top_chats"]) or "无"),
        f"- 新增知识点：{len(stats['knowledge_titles'])} 条",
        f"- 新增待办：{stats['new_todos']} 条；未完成的紧急待办：{stats['open_urgent']} 条",
    ]
    if inactive:
        lines.append("- VIP 动态：" + "、".join(
            f"{name} 自 {last_ts[:10]} 起无消息" if last_ts else f"{name} 无消息记录"
            for name, last_ts in inactive
        ))
    return lines


async def generate_digest(
    period: str, progress=None, task_id: str | None = None, year: int | None = None,
) -> dict:
    """Weekly/monthly windows are relative to now; yearly with an explicit
    `year` covers that calendar year (ending now if it's the current year)."""
    from app.services.analyzer import AnalyzerService

    if period not in PERIODS:
        raise ValueError(f"未知报告周期: {period}")
    days, label, unit = PERIODS[period]
    end = datetime.now().replace(microsecond=0)
    if period == "yearly" and year:
        start = datetime(year, 1, 1)
        year_end = datetime(year, 12, 31, 23, 59, 59)
        end = min(end, year_end)
        days = max(1, (end - start).days + 1)
        label = f"{year} 年报"
    else:
        start = end - timedelta(days=days)
    msg_since = start.isoformat(timespec="seconds")

    def emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    emit(5, "更新统计...")
    try:
        from app.services.stats import refresh as refresh_stats
        await refresh_stats()  # incremental + single-flight; the counts below read the rollups
    except Exception as e:  # stats are best-effort for the narrative
        logger.warning("digest stats refresh failed: %s", e)

    emit(10, "统计本周期数据...")
    msg_until = end.isoformat(timespec="seconds")
    async with database.connect_db() as db:
        stats = await _window_stats(db, msg_since, days, msg_until)
        inactive = await _inactive_vips(db, msg_since)

    stat_lines = _stats_lines(stats, inactive)
    total = sum(n for _, n in stats["platforms"])

    narrative = ""
    if total or stats["knowledge_titles"]:
        emit(40, f"生成{label}正文...")
        svc = AnalyzerService()
        svc.task_id = task_id
        narrative = await svc._call_llm(
            NARRATIVE_PROMPT.format(
                label=label, unit=unit,
                stats="\n".join(stat_lines),
                titles="\n".join(f"- {t}" for t in stats["knowledge_titles"]) or "（无）",
                summaries="\n\n".join(stats["summaries"]) or "（无）",
            ),
            purpose="digest",
        ) or ""
        if not narrative:
            logger.warning("digest narrative failed: %s", svc.last_llm_error)
    else:
        narrative = f"## 本{unit}概览\n\n本周期没有新消息，跳过 AI 总结。"

    date_range = f"{start.date()} ~ {end.date()}"
    content = "\n\n".join(filter(None, [
        f"# {label} {date_range}",
        narrative.strip(),
        "---\n\n## 数据附录\n\n" + "\n".join(stat_lines),
    ]))

    emit(90, "保存报告...")
    async with database.connect_db() as db:
        cur = await db.execute(
            "INSERT INTO reports (period, period_start, period_end, content) VALUES (?,?,?,?)",
            (period, msg_since, msg_until, content),
        )
        await db.commit()
        report_id = cur.lastrowid

    return {"id": report_id, "period": period, "range": date_range, "messages": total}
