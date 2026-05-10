"""Keyword trigger scanner.

After each sync runner completes, scan_for_matches() walks every keyword's
unscanned messages, inserts new (keyword, message_id) rows into
keyword_triggers, and fires a macOS notification per match.

Design:
- One row per (keyword, message) — UNIQUE constraint dedupes if scan re-runs.
- keyword_scan_state tracks the highest message_id scanned per keyword so a
  new keyword backfills against history once, then only sees new messages.
- Notifications use osascript via subprocess; failure is silent (notification
  permission not granted is the common case and shouldn't block sync).
"""
from __future__ import annotations

import asyncio
import logging
import shlex

import aiosqlite

from app.core.config import load_config
from app.core.database import DB_PATH

logger = logging.getLogger("app.triggers")


async def _osascript_notify(title: str, body: str) -> None:
    """Best-effort macOS notification. Runs `osascript` in subprocess and
    swallows errors — first run prompts the user to allow notifications and
    we don't want a "denied" to crash the scanner."""
    # Escape quotes by stripping/replacing — osascript's display notification
    # doesn't tolerate unescaped \" cleanly across platforms.
    safe_title = title.replace('"', "'").replace("\\", " ")[:120]
    safe_body = body.replace('"', "'").replace("\\", " ")[:300]
    script = f'display notification "{safe_body}" with title "{safe_title}"'
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (FileNotFoundError, asyncio.TimeoutError, Exception) as e:
        logger.debug("osascript notify failed: %s", e)


async def scan_for_matches(*, notify: bool = True) -> int:
    """Run a full incremental keyword scan. Returns count of new triggers."""
    cfg = load_config()
    if not cfg.keywords:
        return 0

    new_triggers: list[tuple[str, int, str]] = []  # (keyword, message_id, content_preview)

    async with aiosqlite.connect(str(DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        max_row = await db.execute_fetchall("SELECT COALESCE(MAX(id), 0) AS m FROM messages")
        global_max = int(max_row[0]["m"]) if max_row else 0
        if global_max == 0:
            return 0

        for kw in cfg.keywords:
            kw = kw.strip()
            if not kw:
                continue
            state_row = await db.execute_fetchall(
                "SELECT last_scanned_message_id FROM keyword_scan_state WHERE keyword = ?",
                (kw,),
            )
            since_id = int(state_row[0]["last_scanned_message_id"]) if state_row else 0

            # Pull only id+chat+content for matched candidates so we can cite
            # the message in the notification body without a second query.
            rows = await db.execute_fetchall(
                """SELECT id, chat_name, sender_name, content
                   FROM messages
                   WHERE id > ? AND content LIKE ?
                   ORDER BY id""",
                (since_id, f"%{kw}%"),
            )
            for r in rows:
                cur = await db.execute(
                    "INSERT OR IGNORE INTO keyword_triggers (keyword, message_id) VALUES (?, ?)",
                    (kw, int(r["id"])),
                )
                if cur.rowcount > 0:
                    preview = (r["content"] or "")[:120]
                    sender = r["sender_name"] or ""
                    chat = r["chat_name"] or ""
                    citation = f"{chat} · {sender}: {preview}" if chat else preview
                    new_triggers.append((kw, int(r["id"]), citation))

            await db.execute(
                """INSERT INTO keyword_scan_state (keyword, last_scanned_message_id) VALUES (?, ?)
                   ON CONFLICT(keyword) DO UPDATE SET last_scanned_message_id = excluded.last_scanned_message_id""",
                (kw, global_max),
            )
        await db.commit()

    # Cap notifications: if a new keyword backfills against history we'd spam
    # the user with hundreds. Notify on first 3, then a single "and N more".
    if notify and new_triggers:
        # Group by keyword for cleaner UX.
        for kw, _, citation in new_triggers[:3]:
            await _osascript_notify(f"关键词命中: {kw}", citation)
        if len(new_triggers) > 3:
            await _osascript_notify(
                "关键词命中",
                f"还有 {len(new_triggers) - 3} 条匹配，到收件箱查看",
            )

    return len(new_triggers)


async def rescan_keyword(keyword: str) -> int:
    """Reset scan_state for a keyword and rescan from scratch. Used when the
    user wants historical matches for a freshly-edited rule."""
    async with aiosqlite.connect(str(DB_PATH), timeout=30) as db:
        await db.execute("DELETE FROM keyword_scan_state WHERE keyword = ?", (keyword,))
        await db.execute("DELETE FROM keyword_triggers WHERE keyword = ?", (keyword,))
        await db.commit()
    return await scan_for_matches(notify=False)
