"""红包转账: counts and timeline of red packets / transfers.

Amounts are NOT available — WeChat never puts them in the message body that
wx-cli exports (`[链接] <![CDATA[微信红包]]>`), so this page is about frequency,
direction (sent by me vs. others) and who/where, not money totals.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from app.core import database
from app.api.stats import _fill_months, _filters
from app.services.stats import infer_self_identities

router = APIRouter()

PAY_KINDS = ("redpacket", "transfer")


@router.get("/api/payments")
async def payments_overview(
    platform: Optional[str] = None,
    account: Optional[str] = None,
    chat_id: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 200))
    where, params = _filters(platform, account)
    where += " AND kind IN ('redpacket','transfer')"
    if chat_id:
        where += " AND chat_id = ?"
        params.append(chat_id)
    if since:
        where += " AND timestamp >= ?"
        params.append(since if "T" in since else f"{since}T00:00:00")
    if until:
        where += " AND timestamp <= ?"
        params.append(until if "T" in until else f"{until}T23:59:59")

    async with database.connect_db() as db:
        self_ids = await infer_self_identities(db)
        me_names = {v["sender_name"] for v in self_ids.values() if v.get("sender_name")}
        me_keys = {v["sender_key"] for v in self_ids.values() if v.get("sender_key")}

        totals = await db.execute_fetchall(
            f"SELECT kind, COUNT(*) AS n FROM resources WHERE {where} GROUP BY kind", params,
        )
        monthly = await db.execute_fetchall(
            f"""SELECT substr(timestamp, 1, 7) AS month, kind, COUNT(*) AS n FROM resources
                WHERE {where} GROUP BY month, kind ORDER BY month""",
            params,
        )
        by_chat = await db.execute_fetchall(
            f"""SELECT platform, chat_id, MAX(chat_name) AS chat_name,
                       SUM(CASE WHEN kind = 'redpacket' THEN 1 ELSE 0 END) AS redpacket,
                       SUM(CASE WHEN kind = 'transfer' THEN 1 ELSE 0 END) AS transfer,
                       COUNT(*) AS n, MAX(timestamp) AS last_ts
                FROM resources WHERE {where} GROUP BY platform, chat_id ORDER BY n DESC LIMIT 20""",
            params,
        )
        by_sender = await db.execute_fetchall(
            f"""SELECT platform, COALESCE(NULLIF(sender_name, ''), '(对方)') AS sender_name,
                       SUM(CASE WHEN kind = 'redpacket' THEN 1 ELSE 0 END) AS redpacket,
                       SUM(CASE WHEN kind = 'transfer' THEN 1 ELSE 0 END) AS transfer,
                       COUNT(*) AS n
                FROM resources WHERE {where} GROUP BY platform, sender_name ORDER BY n DESC LIMIT 20""",
            params,
        )
        weekday = await db.execute_fetchall(
            f"""SELECT CAST(strftime('%w', substr(timestamp, 1, 10)) AS INTEGER) AS wd, COUNT(*) AS n
                FROM resources WHERE {where} GROUP BY wd""",
            params,
        )
        hourly = await db.execute_fetchall(
            f"""SELECT CAST(substr(timestamp, 12, 2) AS INTEGER) AS hour, COUNT(*) AS n
                FROM resources WHERE {where} GROUP BY hour""",
            params,
        )
        items = await db.execute_fetchall(
            f"""SELECT id, message_id, platform, chat_id, chat_name, sender_name, timestamp, kind, title
                FROM resources WHERE {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
            [*params, limit + 1, offset],
        )
        # Direction split over the whole window (payment rows are few, so a
        # full pass is cheap).
        direction_rows = await db.execute_fetchall(
            f"SELECT sender_name, kind FROM resources WHERE {where}", params,
        ) if (me_names or me_keys) else []

    counts = {r["kind"]: int(r["n"]) for r in totals}
    by_month: dict[str, dict] = {}
    for r in monthly:
        by_month.setdefault(r["month"], {"month": r["month"], "redpacket": 0, "transfer": 0})[r["kind"]] = int(r["n"])
    month_rows = _fill_months(list(by_month.values()), zero={"redpacket": 0, "transfer": 0})

    def is_me(sender: str | None) -> bool:
        return bool(sender) and (sender in me_names or sender in me_keys)

    direction = None
    sent = 0
    if me_names or me_keys:
        direction = {"redpacket": {"sent": 0, "received": 0}, "transfer": {"sent": 0, "received": 0}}
        for r in direction_rows:
            direction[r["kind"]]["sent" if is_me(r["sender_name"]) else "received"] += 1
        sent = direction["redpacket"]["sent"] + direction["transfer"]["sent"]

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
            "redpacket": counts.get("redpacket", 0),
            "transfer": counts.get("transfer", 0),
            "sent": sent,
        },
        "direction": direction,
        "me": sorted(me_names),
        "monthly": month_rows,
        "by_chat": [dict(r) for r in by_chat],
        "by_sender": [dict(r) for r in by_sender],
        "hourly": hours,
        "weekday": weekdays,
        "items": [{**dict(r), "is_me": is_me(r["sender_name"])} for r in items[:limit]],
        "has_more": len(items) > limit,
    }
