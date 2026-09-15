"""@消息: messages in group chats that mention the user.

Names come from config.self_names plus the per-platform identity inferred
from private chats (services/stats.infer_self_identities). Matching uses the
FTS5 trigram index when available (needs ≥3 chars, which "@" + a 2-char name
satisfies); shorter names fall back to LIKE.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from app.core import database
from app.core.config import load_config
from app.services.stats import infer_self_identities

router = APIRouter()

_MSG_COLS = (
    "m.id, m.platform, m.chat_id, m.chat_name, m.chat_type, m.sender_id, "
    "m.sender_name, m.content, m.msg_type, m.timestamp"
)


async def _names(db) -> dict:
    cfg = load_config()
    inferred = await infer_self_identities(db)
    configured = [n for n in cfg.self_names if n]
    auto = [v["sender_name"] for v in inferred.values() if v.get("sender_name")]
    names: list[str] = []
    for n in [*configured, *auto]:
        if n and n not in names:
            names.append(n)
    return {"configured": configured, "inferred": inferred, "names": names}


def _fts_query(terms: list[str]) -> str:
    # Quote each term as a phrase; FTS5 escapes double quotes by doubling.
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


@router.get("/api/mentions/names")
async def mention_names():
    async with database.connect_db() as db:
        return await _names(db)


@router.get("/api/mentions")
async def list_mentions(
    platform: Optional[str] = None,
    chat_id: Optional[str] = None,
    include_all: bool = False,
    q: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Newest-first mentions with per-chat counts for the sidebar."""
    limit = max(1, min(limit, 200))
    async with database.connect_db() as db:
        info = await _names(db)
        names = list(info["names"])
        if not names and not include_all:
            return {"items": [], "has_more": False, "names": info, "by_chat": [], "total": 0}

        terms = [f"@{n}" for n in names]
        if include_all:
            terms += ["@所有人", "@全体成员", "@everyone", "@all"]

        conds: list[str] = []
        params: list = []
        # Name matching first (it's the selective predicate). Trigram FTS needs
        # ≥3 chars per phrase; anything shorter goes through LIKE.
        if database.FTS_ENABLED:
            fts_terms = [t for t in terms if len(t) >= 3]
            like_terms = [t for t in terms if len(t) < 3]
        else:
            fts_terms, like_terms = [], terms
        name_conds: list[str] = []
        if fts_terms:
            name_conds.append("m.id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)")
            params.append(_fts_query(fts_terms))
        for t in like_terms:
            name_conds.append("m.content LIKE ?")
            params.append(f"%{t}%")
        conds.append("(" + " OR ".join(name_conds) + ")")

        conds.append("m.chat_type <> 'private'")
        if platform:
            conds.append("m.platform = ?")
            params.append(platform)
        if chat_id:
            conds.append("m.chat_id = ?")
            params.append(chat_id)
        if q:
            conds.append("m.content LIKE ?")
            params.append(f"%{q}%")

        where = " AND ".join(conds)
        rows = await db.execute_fetchall(
            f"""SELECT {_MSG_COLS} FROM messages m WHERE {where}
                ORDER BY m.timestamp DESC, m.id DESC LIMIT ? OFFSET ?""",
            [*params, limit + 1, offset],
        )
        by_chat = await db.execute_fetchall(
            f"""SELECT m.platform, m.chat_id, MAX(m.chat_name) AS chat_name, COUNT(*) AS n, MAX(m.timestamp) AS last_ts
                FROM messages m WHERE {where} GROUP BY m.platform, m.chat_id ORDER BY n DESC LIMIT 50""",
            params,
        )

    items = [dict(r) for r in rows[:limit]]
    chats = [dict(r) for r in by_chat]
    return {
        "items": items,
        "has_more": len(rows) > limit,
        "names": {"configured": info["configured"], "inferred": info["inferred"], "names": names},
        "by_chat": chats,
        "total": sum(int(c["n"]) for c in chats),
    }
