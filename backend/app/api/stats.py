"""Read endpoints over the pre-aggregated stats tables (services/stats.py).

Every query here hits stats_* / resources only — never the raw messages table —
so they stay fast at millions of messages. Common filters: `platform`,
`account` (多号), and for the overview `chat_type`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import database
from app.core.tasks import is_task_running
from app.services import stats as stats_svc
from app.services.stats import KIND_LABEL, KINDS

router = APIRouter()


def _filters(
    platform: Optional[str], account: Optional[str], *, alias: str = "",
    chat_type: Optional[str] = None,
) -> tuple[str, list]:
    """Build a WHERE fragment (without the WHERE keyword) for the given dims.
    `alias` qualifies the columns when the query joins stats_chats."""
    pre = f"{alias}." if alias else ""
    conds: list[str] = []
    params: list = []
    if platform:
        conds.append(f"{pre}platform = ?")
        params.append(platform)
    if account is not None and account != "":
        conds.append(f"{pre}account = ?")
        params.append(account)
    if chat_type:
        conds.append("c.chat_type = ?")
        params.append(chat_type)
    return (" AND ".join(conds) or "1=1"), params


def _month_range(first: str, last: str) -> list[str]:
    """Inclusive list of YYYY-MM between two month strings."""
    try:
        y, m = int(first[:4]), int(first[5:7])
        y2, m2 = int(last[:4]), int(last[5:7])
    except (ValueError, TypeError):
        return []
    out = []
    while (y, m) <= (y2, m2) and len(out) < 600:
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def _fill_months(rows: list[dict], month_key: str = "month", zero: dict | None = None) -> list[dict]:
    """Insert zero rows for months with no data so charts keep a linear time axis."""
    if not rows:
        return rows
    by = {r[month_key]: r for r in rows}
    months = _month_range(rows[0][month_key], rows[-1][month_key])
    return [by.get(mo) or {month_key: mo, **(zero or {})} for mo in months]


def _kind_rows(rows) -> list[dict]:
    by_kind = {r["kind"]: int(r["n"]) for r in rows}
    ordered = [k for k in KINDS if k in by_kind] + [k for k in by_kind if k not in KINDS]
    return [{"kind": k, "label": KIND_LABEL.get(k, k), "count": by_kind[k]} for k in ordered]


@router.get("/api/stats/status")
async def stats_status():
    status = await stats_svc.get_status()
    status["running"] = is_task_running("stats")
    return status


@router.post("/api/stats/refresh")
async def stats_refresh(full: bool = False):
    task_id = stats_svc.spawn_refresh_if_needed(full=full)
    if task_id is None:
        raise HTTPException(409, "统计任务已在运行")
    return {"task_id": task_id}


@router.get("/api/stats/accounts")
async def stats_accounts():
    """Distinct (platform, account) pairs seen in the rollups — drives the 多号 switch."""
    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            """SELECT platform, account, SUM(n) AS n, COUNT(*) AS chats
               FROM stats_chats GROUP BY platform, account ORDER BY platform, n DESC"""
        )
    return [
        {"platform": r["platform"], "account": r["account"] or "", "messages": int(r["n"]), "chats": int(r["chats"])}
        for r in rows
    ]


class AccountAssign(BaseModel):
    platform: str
    from_account: str = ""   # '' = rows with no label yet
    to_account: str


@router.post("/api/stats/accounts/assign")
async def assign_account(body: AccountAssign):
    """Relabel a platform's messages from one account label to another (typically
    '' → '主号' after enabling 多号), then rebuild the rollups since account is a
    dimension of every stats table."""
    to = body.to_account.strip()
    if not to:
        raise HTTPException(400, "to_account 不能为空")
    if is_task_running("stats"):
        raise HTTPException(409, "统计任务正在运行，请稍后再试")
    async with database.connect_db() as db:
        cur = await db.execute(
            "UPDATE messages SET account = ? WHERE platform = ? AND COALESCE(account, '') = ?",
            (to, body.platform, body.from_account.strip()),
        )
        await db.commit()
    task_id = stats_svc.spawn_refresh_if_needed(full=True)
    return {"updated": cur.rowcount, "task_id": task_id}


@router.get("/api/stats/overview")
async def stats_overview(
    platform: Optional[str] = None,
    account: Optional[str] = None,
    chat_type: Optional[str] = None,
    exclude_channel: bool = False,
    top: int = 15,
):
    """Everything the 数据概览 page needs in one round-trip. `exclude_channel`
    drops Telegram broadcast channels (one-way feeds, not conversations)."""
    top = max(1, min(top, 50))
    where_c, p_c = _filters(platform, account, alias="c", chat_type=chat_type)
    if exclude_channel:
        where_c += " AND COALESCE(c.chat_type, '') <> 'channel'"
    need_join = bool(chat_type or exclude_channel)
    join_c = (
        "JOIN stats_chats c ON c.account = d.account AND c.platform = d.platform AND c.chat_id = d.chat_id"
        if need_join else ""
    )
    where_d = where_c if need_join else _filters(platform, account, alias="d")[0]

    async with database.connect_db() as db:
        totals = await db.execute_fetchall(
            f"""SELECT COALESCE(SUM(c.n), 0) AS messages,
                       COUNT(*) AS chats,
                       SUM(CASE WHEN c.chat_type = 'group' THEN 1 ELSE 0 END) AS groups,
                       SUM(CASE WHEN c.chat_type = 'private' THEN 1 ELSE 0 END) AS privates,
                       MIN(c.first_ts) AS first_ts, MAX(c.last_ts) AS last_ts
                FROM stats_chats c WHERE {where_c}""",
            p_c,
        )
        monthly = await db.execute_fetchall(
            f"""SELECT substr(d.day, 1, 7) AS month, SUM(d.n) AS n
                FROM stats_daily d {join_c} WHERE {where_d}
                GROUP BY month ORDER BY month""",
            p_c,
        )
        hourly = await db.execute_fetchall(
            f"""SELECT h.hour AS hour, SUM(h.n) AS n
                FROM stats_hourly h
                {'JOIN stats_chats c ON c.account = h.account AND c.platform = h.platform AND c.chat_id = h.chat_id' if need_join else ''}
                WHERE {where_c if need_join else _filters(platform, account, alias='h')[0]}
                GROUP BY h.hour""",
            p_c,
        )
        kinds = await db.execute_fetchall(
            f"""SELECT d.kind AS kind, SUM(d.n) AS n
                FROM stats_daily d {join_c} WHERE {where_d}
                GROUP BY d.kind ORDER BY n DESC""",
            p_c,
        )
        top_rows = await db.execute_fetchall(
            f"""SELECT c.platform, c.chat_id, c.chat_name, c.chat_type, c.account, c.n, c.last_ts
                FROM stats_chats c WHERE {where_c}
                ORDER BY c.n DESC LIMIT ?""",
            [*p_c, top],
        )
        platforms = await db.execute_fetchall(
            f"""SELECT c.platform, SUM(c.n) AS n, COUNT(*) AS chats
                FROM stats_chats c WHERE {where_c} GROUP BY c.platform ORDER BY n DESC""",
            p_c,
        )
        weekday = await db.execute_fetchall(
            f"""SELECT CAST(strftime('%w', d.day) AS INTEGER) AS wd, SUM(d.n) AS n
                FROM stats_daily d {join_c} WHERE {where_d} GROUP BY wd""",
            p_c,
        )

    t = totals[0] if totals else None
    hours = [0] * 24
    for r in hourly:
        if r["hour"] is not None and 0 <= int(r["hour"]) < 24:
            hours[int(r["hour"])] = int(r["n"])
    weekdays = [0] * 7
    for r in weekday:
        if r["wd"] is not None:
            weekdays[int(r["wd"])] = int(r["n"])

    return {
        "totals": {
            "messages": int(t["messages"]) if t else 0,
            "chats": int(t["chats"]) if t else 0,
            "groups": int(t["groups"] or 0) if t else 0,
            "privates": int(t["privates"] or 0) if t else 0,
            "active_months": len(monthly),
            "first_ts": t["first_ts"] if t else None,
            "last_ts": t["last_ts"] if t else None,
        },
        "monthly": _fill_months([{"month": r["month"], "count": int(r["n"])} for r in monthly], zero={"count": 0}),
        "hourly": hours,
        "weekday": weekdays,
        "kinds": _kind_rows(kinds),
        "platforms": [{"platform": r["platform"], "count": int(r["n"]), "chats": int(r["chats"])} for r in platforms],
        "top_chats": [
            {
                "platform": r["platform"], "chat_id": r["chat_id"], "chat_name": r["chat_name"],
                "chat_type": r["chat_type"], "account": r["account"] or "", "count": int(r["n"]),
                "last_ts": r["last_ts"],
            }
            for r in top_rows
        ],
    }


@router.get("/api/stats/calendar")
async def stats_calendar(
    year: Optional[int] = None,
    platform: Optional[str] = None,
    account: Optional[str] = None,
    chat_id: Optional[str] = None,
):
    """Per-day counts for the GitHub-style heatmap plus the list of years with data."""
    where, params = _filters(platform, account)
    if chat_id:
        where += " AND chat_id = ?"
        params.append(chat_id)
    async with database.connect_db() as db:
        years = await db.execute_fetchall(
            f"SELECT DISTINCT substr(day, 1, 4) AS y FROM stats_daily WHERE {where} ORDER BY y DESC",
            params,
        )
        year_list = [int(r["y"]) for r in years if r["y"] and r["y"].isdigit()]
        if year is None:
            year = year_list[0] if year_list else None
        days = []
        if year is not None:
            rows = await db.execute_fetchall(
                f"""SELECT day, SUM(n) AS n FROM stats_daily
                    WHERE {where} AND day >= ? AND day < ? GROUP BY day ORDER BY day""",
                [*params, f"{year}-01-01", f"{year + 1}-01-01"],
            )
            days = [{"day": r["day"], "count": int(r["n"])} for r in rows]
    total = sum(d["count"] for d in days)
    peak = max(days, key=lambda d: d["count"]) if days else None
    return {
        "year": year,
        "years": year_list,
        "days": days,
        "total": total,
        "active_days": len(days),
        "peak": peak,
    }


def _longest_streak(days: list[str]) -> dict:
    """Longest run of consecutive active days: {"days", "start", "end"}."""
    from datetime import date, timedelta

    best = {"days": 0, "start": None, "end": None}
    if not days:
        return best
    run_start = prev = None
    length = 0
    for d in sorted(set(days)):
        try:
            cur = date.fromisoformat(d)
        except ValueError:
            continue
        if prev is not None and cur - prev == timedelta(days=1):
            length += 1
        else:
            run_start, length = cur, 1
        prev = cur
        if length > best["days"]:
            best = {"days": length, "start": run_start.isoformat(), "end": cur.isoformat()}
    return best


@router.get("/api/stats/yearly")
async def stats_yearly(year: Optional[int] = None, platform: Optional[str] = None, account: Optional[str] = None):
    """Everything for the 年度报告 page, computed from the rollups for one calendar year."""
    where, params = _filters(platform, account)
    async with database.connect_db() as db:
        years = await db.execute_fetchall(
            f"SELECT DISTINCT substr(day, 1, 4) AS y FROM stats_daily WHERE {where} ORDER BY y DESC", params,
        )
        year_list = [int(r["y"]) for r in years if r["y"] and r["y"].isdigit()]
        if year is None:
            year = year_list[0] if year_list else None
        if year is None:
            return {"year": None, "years": [], "totals": None}
        lo, hi = f"{year}-01-01", f"{year + 1}-01-01"
        plo, phi = f"{year - 1}-01-01", lo
        yr = [*params, lo, hi]

        days = await db.execute_fetchall(
            f"SELECT day, SUM(n) AS n FROM stats_daily WHERE {where} AND day >= ? AND day < ? GROUP BY day ORDER BY day", yr,
        )
        prev_total = await db.execute_fetchall(
            f"SELECT COALESCE(SUM(n), 0) AS n FROM stats_daily WHERE {where} AND day >= ? AND day < ?", [*params, plo, phi],
        )
        kinds = await db.execute_fetchall(
            f"SELECT kind, SUM(n) AS n FROM stats_daily WHERE {where} AND day >= ? AND day < ? GROUP BY kind ORDER BY n DESC", yr,
        )
        weekday = await db.execute_fetchall(
            f"""SELECT CAST(strftime('%w', day) AS INTEGER) AS wd, SUM(n) AS n FROM stats_daily
                WHERE {where} AND day >= ? AND day < ? GROUP BY wd""", yr,
        )
        chats = await db.execute_fetchall(
            f"""SELECT d.platform, d.chat_id, c.chat_name, c.chat_type, SUM(d.n) AS n,
                       COUNT(DISTINCT d.day) AS active_days
                FROM stats_daily d
                JOIN stats_chats c ON c.account = d.account AND c.platform = d.platform AND c.chat_id = d.chat_id
                WHERE {_filters(platform, account, alias='d')[0]} AND d.day >= ? AND d.day < ?
                GROUP BY d.platform, d.chat_id ORDER BY n DESC""", yr,
        )
        new_chats = await db.execute_fetchall(
            f"""SELECT COUNT(*) AS c, SUM(CASE WHEN chat_type = 'group' THEN 1 ELSE 0 END) AS g
                FROM stats_chats WHERE {where} AND first_ts >= ? AND first_ts < ?""", yr,
        )
        # Hour-of-day for the year: stats_hourly is all-time, so approximate the
        # year's hour profile from the chats active this year, weighted by their
        # share of activity in the year. Exact enough for a "你最爱在几点聊" card.
        hourly = await db.execute_fetchall(
            f"""SELECT h.hour, SUM(h.n * 1.0 * y.n / NULLIF(c.n, 0)) AS n
                FROM stats_hourly h
                JOIN stats_chats c ON c.account = h.account AND c.platform = h.platform AND c.chat_id = h.chat_id
                JOIN (SELECT account, platform, chat_id, SUM(n) AS n FROM stats_daily
                      WHERE day >= ? AND day < ? GROUP BY account, platform, chat_id) y
                  ON y.account = h.account AND y.platform = h.platform AND y.chat_id = h.chat_id
                WHERE {_filters(platform, account, alias='h')[0]}
                GROUP BY h.hour""",
            [lo, hi, *params],
        )
        senders = await db.execute_fetchall(
            f"""SELECT s.platform, s.chat_id, c.chat_name, s.sender_key,
                       MAX(s.sender_name) AS sender_name, SUM(s.n) AS n
                FROM stats_sender s
                JOIN stats_chats c ON c.account = s.account AND c.platform = s.platform AND c.chat_id = s.chat_id
                WHERE {_filters(platform, account, alias='s')[0]} AND s.month >= ? AND s.month < ?
                  AND c.chat_type <> 'private' AND s.sender_key <> '?'
                GROUP BY s.platform, s.chat_id, s.sender_key ORDER BY n DESC LIMIT 10""",
            [*params, f"{year}-01", f"{year + 1}-01"],
        )
        res_counts = await db.execute_fetchall(
            f"""SELECT kind, COUNT(*) AS n FROM resources WHERE {where} AND timestamp >= ? AND timestamp < ?
                GROUP BY kind""", yr,
        )

    day_rows = [{"day": r["day"], "count": int(r["n"])} for r in days]
    total = sum(d["count"] for d in day_rows)
    monthly = [{"month": f"{year}-{m:02d}", "count": 0} for m in range(1, 13)]
    for d in day_rows:
        monthly[int(d["day"][5:7]) - 1]["count"] += d["count"]
    hours = [0.0] * 24
    for r in hourly:
        if r["hour"] is not None and 0 <= int(r["hour"]) < 24:
            hours[int(r["hour"])] = round(float(r["n"] or 0))
    weekdays = [0] * 7
    for r in weekday:
        if r["wd"] is not None:
            weekdays[int(r["wd"])] = int(r["n"])
    peak_day = max(day_rows, key=lambda d: d["count"]) if day_rows else None
    peak_month = max(monthly, key=lambda m: m["count"]) if total else None
    prev = int(prev_total[0]["n"]) if prev_total else 0
    chat_rows = [
        {"platform": r["platform"], "chat_id": r["chat_id"], "chat_name": r["chat_name"], "chat_type": r["chat_type"],
         "count": int(r["n"]), "active_days": int(r["active_days"])}
        for r in chats
    ]
    return {
        "year": year,
        "years": year_list,
        "totals": {
            "messages": total,
            "prev_messages": prev,
            "delta_pct": round((total - prev) / prev * 100, 1) if prev else None,
            "active_days": len(day_rows),
            "avg_per_active_day": round(total / len(day_rows), 1) if day_rows else 0,
            "chats": len(chat_rows),
            "groups": sum(1 for c in chat_rows if c["chat_type"] == "group"),
            "new_chats": int(new_chats[0]["c"] or 0) if new_chats else 0,
            "new_groups": int(new_chats[0]["g"] or 0) if new_chats else 0,
        },
        "monthly": monthly,
        "hourly": [int(h) for h in hours],
        "weekday": weekdays,
        "kinds": _kind_rows(kinds),
        "days": day_rows,
        "peak_day": peak_day,
        "peak_month": peak_month,
        "peak_hour": int(max(range(24), key=lambda h: hours[h])) if any(hours) else None,
        "streak": _longest_streak([d["day"] for d in day_rows]),
        "top_chats": chat_rows[:10],
        "top_groups": [c for c in chat_rows if c["chat_type"] != "private"][:10],
        "top_privates": [c for c in chat_rows if c["chat_type"] == "private"][:10],
        "top_speakers": [
            {"platform": r["platform"], "chat_id": r["chat_id"], "chat_name": r["chat_name"],
             "name": r["sender_name"] or r["sender_key"], "count": int(r["n"])}
            for r in senders
        ],
        "resources": {r["kind"]: int(r["n"]) for r in res_counts},
    }


@router.get("/api/stats/chat/{platform}/{chat_id}")
async def stats_chat(platform: str, chat_id: str, account: Optional[str] = None):
    """Per-chat rollup for the contact / group profile pages."""
    where, params = _filters(platform, account)
    where += " AND chat_id = ?"
    params.append(chat_id)
    async with database.connect_db() as db:
        meta = await db.execute_fetchall(
            f"""SELECT chat_name, chat_type, SUM(n) AS n, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts
                FROM stats_chats WHERE {where}""",
            params,
        )
        if not meta or not meta[0]["n"]:
            raise HTTPException(404, "没有该聊天的统计数据")
        monthly = await db.execute_fetchall(
            f"""SELECT substr(day, 1, 7) AS month, SUM(n) AS n FROM stats_daily
                WHERE {where} GROUP BY month ORDER BY month""",
            params,
        )
        hourly = await db.execute_fetchall(
            f"SELECT hour, SUM(n) AS n FROM stats_hourly WHERE {where} GROUP BY hour", params,
        )
        kinds = await db.execute_fetchall(
            f"SELECT kind, SUM(n) AS n FROM stats_daily WHERE {where} GROUP BY kind ORDER BY n DESC", params,
        )
        weekday = await db.execute_fetchall(
            f"""SELECT CAST(strftime('%w', day) AS INTEGER) AS wd, SUM(n) AS n FROM stats_daily
                WHERE {where} GROUP BY wd""",
            params,
        )
        senders = await db.execute_fetchall(
            f"SELECT COUNT(DISTINCT sender_key) AS c FROM stats_sender WHERE {where}", params,
        )
        recent = await db.execute_fetchall(
            f"""SELECT day, SUM(n) AS n FROM stats_daily
                WHERE {where} AND day >= date('now', 'localtime', '-90 days')
                GROUP BY day ORDER BY day""",
            params,
        )
    m = meta[0]
    hours = [0] * 24
    for r in hourly:
        if r["hour"] is not None and 0 <= int(r["hour"]) < 24:
            hours[int(r["hour"])] = int(r["n"])
    weekdays = [0] * 7
    for r in weekday:
        if r["wd"] is not None:
            weekdays[int(r["wd"])] = int(r["n"])
    return {
        "platform": platform,
        "chat_id": chat_id,
        "chat_name": m["chat_name"],
        "chat_type": m["chat_type"],
        "messages": int(m["n"]),
        "first_ts": m["first_ts"],
        "last_ts": m["last_ts"],
        "distinct_senders": int(senders[0]["c"]) if senders else 0,
        "monthly": _fill_months([{"month": r["month"], "count": int(r["n"])} for r in monthly], zero={"count": 0}),
        "hourly": hours,
        "weekday": weekdays,
        "kinds": _kind_rows(kinds),
        "recent_days": [{"day": r["day"], "count": int(r["n"])} for r in recent],
    }


@router.get("/api/stats/members/{platform}/{chat_id}")
async def stats_members(
    platform: str,
    chat_id: str,
    account: Optional[str] = None,
    limit: int = 50,
    trend_top: int = 6,
    inactive_days: int = 90,
):
    """Group member analysis: ranking, per-member hours, monthly trend of the
    top N, and who has gone quiet."""
    limit = max(1, min(limit, 500))
    trend_top = max(1, min(trend_top, 12))
    where, params = _filters(platform, account)
    where += " AND chat_id = ?"
    params.append(chat_id)
    # '?' = messages without any sender (system notices, wx-cli blanks) — not a member.
    where_s = where + " AND sender_key <> '?'"
    async with database.connect_db() as db:
        total_row = await db.execute_fetchall(
            f"SELECT SUM(n) AS n, MAX(last_ts) AS last_ts FROM stats_chats WHERE {where}", params,
        )
        total = int(total_row[0]["n"] or 0) if total_row else 0
        if not total:
            raise HTTPException(404, "没有该聊天的统计数据")
        chat_last = total_row[0]["last_ts"] or ""

        rank = await db.execute_fetchall(
            f"""SELECT sender_key,
                       -- display name = name attached to the most recent month row
                       (SELECT s2.sender_name FROM stats_sender s2
                         WHERE s2.account = s.account AND s2.platform = s.platform
                           AND s2.chat_id = s.chat_id AND s2.sender_key = s.sender_key
                           AND s2.sender_name IS NOT NULL
                         ORDER BY s2.last_ts DESC LIMIT 1) AS sender_name,
                       SUM(n) AS n, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts,
                       COUNT(DISTINCT month) AS active_months
                FROM stats_sender s WHERE {where_s}
                GROUP BY sender_key ORDER BY n DESC LIMIT ?""",
            [*params, limit],
        )
        member_total = await db.execute_fetchall(
            f"SELECT COUNT(DISTINCT sender_key) AS c FROM stats_sender WHERE {where_s}", params,
        )
        keys = [r["sender_key"] for r in rank]
        hours_by_key: dict[str, list[int]] = {k: [0] * 24 for k in keys}
        if keys:
            ph = ",".join("?" * len(keys))
            hrows = await db.execute_fetchall(
                f"""SELECT sender_key, hour, SUM(n) AS n FROM stats_sender_hourly
                    WHERE {where} AND sender_key IN ({ph}) GROUP BY sender_key, hour""",
                [*params, *keys],
            )
            for r in hrows:
                h = int(r["hour"])
                if 0 <= h < 24:
                    hours_by_key[r["sender_key"]][h] = int(r["n"])

        trend_keys = keys[:trend_top]
        trend: list[dict] = []
        if trend_keys:
            ph = ",".join("?" * len(trend_keys))
            trows = await db.execute_fetchall(
                f"""SELECT month, sender_key, SUM(n) AS n FROM stats_sender
                    WHERE {where} AND sender_key IN ({ph}) GROUP BY month, sender_key ORDER BY month""",
                [*params, *trend_keys],
            )
            by_month: dict[str, dict] = {}
            for r in trows:
                by_month.setdefault(r["month"], {"month": r["month"]})[r["sender_key"]] = int(r["n"])
            trend = _fill_months(list(by_month.values()), zero={k: 0 for k in trend_keys})
            for row in trend:
                for k in trend_keys:
                    row.setdefault(k, 0)

        monthly_active = await db.execute_fetchall(
            f"""SELECT month, COUNT(DISTINCT sender_key) AS c, SUM(n) AS n FROM stats_sender
                WHERE {where_s} GROUP BY month ORDER BY month""",
            params,
        )

    members = []
    for r in rank:
        n = int(r["n"])
        hours = hours_by_key.get(r["sender_key"], [0] * 24)
        night = sum(hours[0:6])
        peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None
        members.append({
            "sender_key": r["sender_key"],
            "name": r["sender_name"] or r["sender_key"],
            "count": n,
            "share": round(n / total * 100, 2) if total else 0,
            "first_ts": r["first_ts"],
            "last_ts": r["last_ts"],
            "active_months": int(r["active_months"]),
            "hours": hours,
            "peak_hour": peak_hour,
            "night_ratio": round(night / n * 100, 1) if n else 0,
        })

    # "Inactive": had messages before but nothing in the last `inactive_days`
    # relative to the chat's latest message (not wall-clock, so stale syncs
    # don't mark everyone quiet).
    cutoff = None
    inactive: list[dict] = []
    if chat_last and len(chat_last) >= 10:
        from datetime import datetime, timedelta
        try:
            cutoff = (datetime.fromisoformat(chat_last[:19]) - timedelta(days=inactive_days)).isoformat(timespec="seconds")
        except ValueError:
            cutoff = None
    if cutoff:
        inactive = [
            {"name": m["name"], "sender_key": m["sender_key"], "count": m["count"], "last_ts": m["last_ts"]}
            for m in members if (m["last_ts"] or "") < cutoff
        ]

    return {
        "platform": platform,
        "chat_id": chat_id,
        "total_messages": total,
        "member_count": int(member_total[0]["c"]) if member_total else len(members),
        "members": members,
        "trend": trend,
        "trend_keys": [{"sender_key": k, "name": next((m["name"] for m in members if m["sender_key"] == k), k)} for k in trend_keys],
        "monthly_active": _fill_months(
            [{"month": r["month"], "members": int(r["c"]), "count": int(r["n"])} for r in monthly_active],
            zero={"members": 0, "count": 0},
        ),
        "inactive": inactive,
        "inactive_days": inactive_days,
    }


@router.get("/api/resources")
async def list_resources(
    kind: Optional[str] = None,
    platform: Optional[str] = None,
    account: Optional[str] = None,
    chat_id: Optional[str] = None,
    q: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 200))
    where, params = _filters(platform, account)
    if chat_id:
        where += " AND chat_id = ?"
        params.append(chat_id)
    if q:
        where += " AND (title LIKE ? OR url LIKE ? OR chat_name LIKE ? OR sender_name LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])
    if since:
        where += " AND timestamp >= ?"
        params.append(since if "T" in since else f"{since}T00:00:00")
    if until:
        where += " AND timestamp <= ?"
        params.append(until if "T" in until else f"{until}T23:59:59")
    # Kind tabs show counts under the *other* filters, so the kind condition is
    # applied to the item list only.
    where_items, params_items = where, list(params)
    if kind:
        kinds = [k for k in kind.split(",") if k]
        where_items += f" AND kind IN ({','.join('?' * len(kinds))})"
        params_items.extend(kinds)
    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            f"""SELECT id, message_id, platform, chat_id, chat_name, sender_name, timestamp, kind, url, title
                FROM resources WHERE {where_items} ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
            [*params_items, limit + 1, offset],
        )
        counts = await db.execute_fetchall(
            f"SELECT kind, COUNT(*) AS n FROM resources WHERE {where} GROUP BY kind", params,
        )
    items = [dict(r) for r in rows[:limit]]
    return {
        "items": items,
        "has_more": len(rows) > limit,
        "counts": {r["kind"]: int(r["n"]) for r in counts},
    }


@router.get("/api/resources/chats")
async def resource_chats(kind: Optional[str] = None, platform: Optional[str] = None, account: Optional[str] = None):
    """Chats that contributed resources, for the resources page filter."""
    where, params = _filters(platform, account)
    if kind:
        kinds = [k for k in kind.split(",") if k]
        where += f" AND kind IN ({','.join('?' * len(kinds))})"
        params.extend(kinds)
    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            f"""SELECT platform, chat_id, MAX(chat_name) AS chat_name, COUNT(*) AS n
                FROM resources WHERE {where} GROUP BY platform, chat_id ORDER BY n DESC LIMIT 200""",
            params,
        )
    return [dict(r) for r in rows]
