"""People across platforms and chats.

A `person` owns `person_identities`; everything that needs "who is this"
(VIP resolution, the contact profile, group footprints) goes through here.
Identity kinds:
  chat   — value = chat_id of the private chat with them
  sender — value = sender_key as seen inside groups; chat_id scopes it to one
           group, '' means platform-wide (WeChat exposes only nicknames)
"""
from __future__ import annotations

import aiosqlite

IDENTITY_COLS = "id, person_id, platform, kind, value, chat_id, display_name, source, created_at"


async def vip_identity_values(db: aiosqlite.Connection, names: list[str]) -> set[str]:
    """Every id / display name attached to persons named in `names`. Used by the
    pre-filter and analyzer so VIP status survives nickname changes."""
    if not names:
        return set()
    ph = ",".join("?" * len(names))
    rows = await db.execute_fetchall(
        f"""SELECT i.value, i.display_name FROM person_identities i
            JOIN persons p ON p.id = i.person_id WHERE p.name IN ({ph})""",
        names,
    )
    return {v for r in rows for v in (r[0], r[1]) if v}


async def person_for_chat(db: aiosqlite.Connection, platform: str, chat_id: str) -> dict | None:
    rows = await db.execute_fetchall(
        """SELECT p.id, p.name, p.notes FROM persons p
           JOIN person_identities i ON i.person_id = p.id
           WHERE i.platform = ? AND i.kind = 'chat' AND i.value = ? LIMIT 1""",
        (platform, chat_id),
    )
    return dict(rows[0]) if rows else None


async def identities_of(db: aiosqlite.Connection, person_id: int) -> list[dict]:
    rows = await db.execute_fetchall(
        f"SELECT {IDENTITY_COLS} FROM person_identities WHERE person_id = ? ORDER BY platform, kind, value",
        (person_id,),
    )
    return [dict(r) for r in rows]


async def footprint(db: aiosqlite.Connection, person_id: int) -> dict:
    """Where this person shows up, from the stats rollups: their private chats
    and every group they have spoken in, with counts and recency."""
    idents = await identities_of(db, person_id)
    chats: list[dict] = []
    groups: list[dict] = []
    total = 0
    for ident in idents:
        if ident["kind"] == "chat":
            rows = await db.execute_fetchall(
                """SELECT platform, chat_id, chat_name, chat_type, SUM(n) AS n, MIN(first_ts) AS first_ts, MAX(last_ts) AS last_ts
                   FROM stats_chats WHERE platform = ? AND chat_id = ? GROUP BY platform, chat_id""",
                (ident["platform"], ident["value"]),
            )
            for r in rows:
                chats.append(dict(r) | {"identity_id": ident["id"]})
                total += int(r["n"])
        else:
            scope = " AND s.chat_id = ?" if ident["chat_id"] else ""
            params = [ident["platform"], ident["value"]] + ([ident["chat_id"]] if ident["chat_id"] else [])
            rows = await db.execute_fetchall(
                f"""SELECT s.platform, s.chat_id, c.chat_name, c.chat_type, SUM(s.n) AS n,
                           MIN(s.first_ts) AS first_ts, MAX(s.last_ts) AS last_ts, COUNT(DISTINCT s.month) AS months
                    FROM stats_sender s
                    JOIN stats_chats c ON c.account = s.account AND c.platform = s.platform AND c.chat_id = s.chat_id
                    WHERE s.platform = ? AND s.sender_key = ?{scope} AND c.chat_type <> 'private'
                    GROUP BY s.platform, s.chat_id ORDER BY n DESC""",
                params,
            )
            for r in rows:
                groups.append(dict(r) | {"identity_id": ident["id"]})
                total += int(r["n"])
    groups.sort(key=lambda g: -int(g["n"]))
    return {"identities": idents, "chats": chats, "groups": groups, "total_messages": total}


async def suggest_for_chat(db: aiosqlite.Connection, platform: str, chat_id: str, chat_name: str | None) -> list[dict]:
    """Candidate sender identities for the person behind a private chat: group
    senders on the same platform whose nickname equals the chat name, or whose
    sender_key equals the chat id (platforms that expose stable ids)."""
    if not chat_name and not chat_id:
        return []
    rows = await db.execute_fetchall(
        """SELECT s.sender_key, MAX(s.sender_name) AS sender_name, COUNT(DISTINCT s.chat_id) AS groups, SUM(s.n) AS n
           FROM stats_sender s
           JOIN stats_chats c ON c.account = s.account AND c.platform = s.platform AND c.chat_id = s.chat_id
           WHERE s.platform = ? AND c.chat_type <> 'private' AND s.sender_key <> '?'
             AND (s.sender_key = ? OR s.sender_name = ?)
           GROUP BY s.sender_key ORDER BY n DESC LIMIT 5""",
        (platform, chat_id, chat_name or ""),
    )
    taken = await db.execute_fetchall(
        "SELECT value FROM person_identities WHERE platform = ? AND kind = 'sender' AND chat_id = ''", (platform,),
    )
    taken_keys = {r[0] for r in taken}
    return [
        {
            "platform": platform, "kind": "sender", "value": r["sender_key"], "chat_id": "",
            "display_name": r["sender_name"], "groups": int(r["groups"]), "messages": int(r["n"]),
            "reason": "群内昵称与私聊名相同" if r["sender_name"] == chat_name else "发送者 id 与聊天 id 相同",
            "already_linked": r["sender_key"] in taken_keys,
        }
        for r in rows
    ]


async def ensure_person(db: aiosqlite.Connection, name: str) -> int:
    rows = await db.execute_fetchall("SELECT id FROM persons WHERE name = ?", (name,))
    if rows:
        return int(rows[0][0])
    cur = await db.execute("INSERT INTO persons (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


async def add_identity(
    db: aiosqlite.Connection, person_id: int, *, platform: str, kind: str, value: str,
    chat_id: str = "", display_name: str | None = None, source: str = "manual",
) -> None:
    if kind not in ("chat", "sender"):
        raise ValueError("kind must be 'chat' or 'sender'")
    await db.execute(
        """INSERT INTO person_identities (person_id, platform, kind, value, chat_id, display_name, source)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(platform, kind, value, chat_id) DO UPDATE SET
             person_id = excluded.person_id,
             display_name = COALESCE(excluded.display_name, person_identities.display_name),
             source = excluded.source""",
        (person_id, platform, kind, value, chat_id or "", display_name, source),
    )


async def merge(db: aiosqlite.Connection, source_id: int, into_id: int) -> None:
    """Move all identities of `source_id` onto `into_id`, then delete the source."""
    await db.execute("UPDATE person_identities SET person_id = ? WHERE person_id = ?", (into_id, source_id))
    await db.execute("DELETE FROM persons WHERE id = ?", (source_id,))


async def prune_empty(db: aiosqlite.Connection, person_id: int) -> bool:
    """Delete the person if no identities remain. Returns True when deleted."""
    rows = await db.execute_fetchall("SELECT COUNT(*) FROM person_identities WHERE person_id = ?", (person_id,))
    if rows and int(rows[0][0]) == 0:
        await db.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        return True
    return False
