"""联系人档案: contact list + one-shot profile composed from the stats rollups,
tags, aliases, cached AI summary, knowledge items and recent messages."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core import database
from app.core.config import load_config
from app.api.stats import _fill_months, _kind_rows
from app.services.stats import infer_self_identities

router = APIRouter()


@router.get("/api/contacts")
async def list_contacts(
    platform: Optional[str] = None,
    account: Optional[str] = None,
    chat_type: Optional[str] = None,
    q: Optional[str] = None,
    tag_id: Optional[int] = None,
    sort: str = "messages",
    limit: int = 500,
):
    """Contacts (and groups/channels) from stats_chats with tag chips attached."""
    limit = max(1, min(limit, 2000))
    conds = ["1=1"]
    params: list = []
    if platform:
        conds.append("c.platform = ?")
        params.append(platform)
    if account:
        conds.append("c.account = ?")
        params.append(account)
    if chat_type:
        conds.append("c.chat_type = ?")
        params.append(chat_type)
    if q:
        conds.append("(c.chat_name LIKE ? OR c.chat_id LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    if tag_id is not None:
        conds.append(
            """EXISTS (SELECT 1 FROM contact_tag_links l WHERE l.platform = c.platform
                       AND l.chat_id = c.chat_id AND l.tag_id = ? AND l.status = 'confirmed')"""
        )
        params.append(tag_id)
    order = {"messages": "c.n DESC", "recent": "c.last_ts DESC", "name": "c.chat_name COLLATE NOCASE ASC"}.get(sort, "c.n DESC")

    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            f"""SELECT c.platform, c.chat_id, c.chat_name, c.chat_type, c.account, c.n, c.first_ts, c.last_ts
                FROM stats_chats c WHERE {' AND '.join(conds)} ORDER BY {order} LIMIT ?""",
            [*params, limit],
        )
        tag_rows = await db.execute_fetchall(
            """SELECT l.platform, l.chat_id, t.id AS tag_id, t.name, t.color
               FROM contact_tag_links l JOIN contact_tags t ON t.id = l.tag_id
               WHERE l.status = 'confirmed'"""
        )
        alias_rows = await db.execute_fetchall(
            "SELECT canonical_name, platform, platform_id, platform_name FROM contact_aliases"
        )

    tags_by: dict[tuple[str, str], list[dict]] = {}
    for r in tag_rows:
        tags_by.setdefault((r["platform"], r["chat_id"]), []).append(
            {"tag_id": r["tag_id"], "name": r["name"], "color": r["color"]}
        )
    alias_by_id = {(r["platform"], r["platform_id"]): r["canonical_name"] for r in alias_rows}
    alias_by_name = {(r["platform"], r["platform_name"]): r["canonical_name"] for r in alias_rows if r["platform_name"]}
    vips = set(load_config().vip_contacts)

    out = []
    for r in rows:
        key = (r["platform"], r["chat_id"])
        canonical = alias_by_id.get(key) or alias_by_name.get((r["platform"], r["chat_name"]))
        out.append({
            "platform": r["platform"], "chat_id": r["chat_id"], "chat_name": r["chat_name"],
            "chat_type": r["chat_type"], "account": r["account"] or "", "messages": int(r["n"]),
            "first_ts": r["first_ts"], "last_ts": r["last_ts"],
            "tags": tags_by.get(key, []),
            "canonical_name": canonical,
            "is_vip": bool((r["chat_name"] and r["chat_name"] in vips) or (canonical and canonical in vips)),
        })
    return out


@router.get("/api/contacts/{platform}/{chat_id}/profile")
async def contact_profile(platform: str, chat_id: str, account: Optional[str] = None):
    acc_cond = " AND account = ?" if account else ""
    acc_params: list = [account] if account else []
    base = [platform, chat_id, *acc_params]

    async with database.connect_db() as db:
        meta = await db.execute_fetchall(
            f"""SELECT chat_name, chat_type, SUM(n) AS n, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts
                FROM stats_chats WHERE platform = ? AND chat_id = ?{acc_cond}""",
            base,
        )
        if not meta or not meta[0]["n"]:
            raise HTTPException(404, "没有该联系人的统计数据（可能统计尚未构建）")
        m = meta[0]
        chat_name = m["chat_name"] or ""

        monthly = await db.execute_fetchall(
            f"""SELECT substr(day, 1, 7) AS month, SUM(n) AS n FROM stats_daily
                WHERE platform = ? AND chat_id = ?{acc_cond} GROUP BY month ORDER BY month""",
            base,
        )
        hourly = await db.execute_fetchall(
            f"SELECT hour, SUM(n) AS n FROM stats_hourly WHERE platform = ? AND chat_id = ?{acc_cond} GROUP BY hour",
            base,
        )
        weekday = await db.execute_fetchall(
            f"""SELECT CAST(strftime('%w', day) AS INTEGER) AS wd, SUM(n) AS n FROM stats_daily
                WHERE platform = ? AND chat_id = ?{acc_cond} GROUP BY wd""",
            base,
        )
        kinds = await db.execute_fetchall(
            f"""SELECT kind, SUM(n) AS n FROM stats_daily WHERE platform = ? AND chat_id = ?{acc_cond}
                GROUP BY kind ORDER BY n DESC""",
            base,
        )
        senders = await db.execute_fetchall(
            f"""SELECT sender_key,
                       (SELECT s2.sender_name FROM stats_sender s2
                         WHERE s2.platform = s.platform AND s2.chat_id = s.chat_id
                           AND s2.sender_key = s.sender_key AND s2.sender_name IS NOT NULL
                         ORDER BY s2.last_ts DESC LIMIT 1) AS sender_name,
                       SUM(n) AS n, MAX(last_ts) AS last_ts
                FROM stats_sender s WHERE platform = ? AND chat_id = ?{acc_cond}
                GROUP BY sender_key ORDER BY n DESC LIMIT 6""",
            base,
        )
        sender_count = await db.execute_fetchall(
            f"""SELECT COUNT(DISTINCT sender_key) AS c FROM stats_sender
                WHERE platform = ? AND chat_id = ?{acc_cond} AND sender_key <> '?'""",
            base,
        )
        active_days = await db.execute_fetchall(
            f"SELECT COUNT(DISTINCT day) AS c FROM stats_daily WHERE platform = ? AND chat_id = ?{acc_cond}",
            base,
        )
        tags = await db.execute_fetchall(
            """SELECT l.id AS link_id, l.confidence, l.reason, l.source, l.status,
                      t.id AS tag_id, t.name, t.color
               FROM contact_tag_links l JOIN contact_tags t ON t.id = l.tag_id
               WHERE l.platform = ? AND l.chat_id = ? ORDER BY l.status, l.confidence DESC""",
            (platform, chat_id),
        )
        aliases = await db.execute_fetchall(
            """SELECT id, canonical_name, platform, platform_id, platform_name FROM contact_aliases
               WHERE (platform = ? AND platform_id = ?) OR (platform = ? AND platform_name = ?)""",
            (platform, chat_id, platform, chat_name),
        )
        canonical = aliases[0]["canonical_name"] if aliases else None
        linked = []
        if canonical:
            linked = await db.execute_fetchall(
                """SELECT id, canonical_name, platform, platform_id, platform_name FROM contact_aliases
                   WHERE canonical_name = ? ORDER BY platform""",
                (canonical,),
            )
        profile = await db.execute_fetchall(
            "SELECT summary, summary_generated_at FROM chat_profiles WHERE platform = ? AND chat_id = ?",
            (platform, chat_id),
        )
        knowledge = await db.execute_fetchall(
            """SELECT id, title, content, tags, created_at FROM knowledge_items
               WHERE source_chat = ? ORDER BY created_at DESC LIMIT 20""",
            (chat_name,),
        ) if chat_name else []
        todos = await db.execute_fetchall(
            """SELECT COUNT(*) AS total, SUM(CASE WHEN a.done = 0 THEN 1 ELSE 0 END) AS open_count
               FROM analysis_results a JOIN messages mm ON mm.id = a.message_id
               WHERE a.category = 'todo' AND mm.platform = ? AND mm.chat_id = ?""",
            (platform, chat_id),
        )
        res_counts = await db.execute_fetchall(
            f"""SELECT kind, COUNT(*) AS n FROM resources WHERE platform = ? AND chat_id = ?{acc_cond}
                GROUP BY kind""",
            base,
        )
        recent = await db.execute_fetchall(
            """SELECT id, sender_name, content, msg_type, timestamp FROM messages
               WHERE platform = ? AND chat_id = ? ORDER BY timestamp DESC, id DESC LIMIT 12""",
            (platform, chat_id),
        )
        self_ids = await infer_self_identities(db)

    me_key = (self_ids.get(platform) or {}).get("sender_key")
    hours = [0] * 24
    for r in hourly:
        if r["hour"] is not None and 0 <= int(r["hour"]) < 24:
            hours[int(r["hour"])] = int(r["n"])
    weekdays = [0] * 7
    for r in weekday:
        if r["wd"] is not None:
            weekdays[int(r["wd"])] = int(r["n"])

    total = int(m["n"])
    sender_list = []
    for r in senders:
        is_me = me_key is not None and r["sender_key"] == me_key
        # wx-cli leaves the sender blank on the contact's own private messages
        # (key '?'); show the chat name instead of a question mark.
        name = r["sender_name"] or (chat_name if r["sender_key"] == "?" and m["chat_type"] == "private" else r["sender_key"])
        sender_list.append({
            "sender_key": r["sender_key"], "name": name,
            "count": int(r["n"]), "share": round(int(r["n"]) / total * 100, 1) if total else 0,
            "last_ts": r["last_ts"],
            "is_me": is_me,
            "is_contact": not is_me,
        })

    vips = set(load_config().vip_contacts)
    days_active = int(active_days[0]["c"]) if active_days else 0
    span_days = None
    if m["first_ts"] and m["last_ts"]:
        from datetime import datetime
        try:
            span_days = (datetime.fromisoformat(m["last_ts"][:19]) - datetime.fromisoformat(m["first_ts"][:19])).days + 1
        except ValueError:
            span_days = None

    return {
        "platform": platform,
        "chat_id": chat_id,
        "chat_name": chat_name,
        "chat_type": m["chat_type"],
        "messages": total,
        "first_ts": m["first_ts"],
        "last_ts": m["last_ts"],
        "active_days": days_active,
        "distinct_senders": int(sender_count[0]["c"]) if sender_count else 0,
        "span_days": span_days,
        "active_months": len(monthly),
        "monthly": _fill_months([{"month": r["month"], "count": int(r["n"])} for r in monthly], zero={"count": 0}),
        "hourly": hours,
        "weekday": weekdays,
        "kinds": _kind_rows(kinds),
        "senders": sender_list,
        "tags": [dict(r) for r in tags],
        "canonical_name": canonical,
        "aliases": [dict(r) for r in (linked or aliases)],
        "is_vip": bool((chat_name and chat_name in vips) or (canonical and canonical in vips)),
        "summary": profile[0]["summary"] if profile else None,
        "summary_generated_at": profile[0]["summary_generated_at"] if profile else None,
        "knowledge": [dict(r) for r in knowledge],
        "todos": {"total": int(todos[0]["total"] or 0), "open": int(todos[0]["open_count"] or 0)} if todos else {"total": 0, "open": 0},
        "resources": {r["kind"]: int(r["n"]) for r in res_counts},
        "recent_messages": [dict(r) for r in recent],
    }
