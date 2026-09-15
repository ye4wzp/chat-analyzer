"""Inbox trigger scanner: keyword matches plus embedding-based semantic matches.

After each sync runner completes, scan_for_matches() walks every trigger's
unscanned messages, inserts new (keyword, message_id) rows into
keyword_triggers, and fires a macOS notification per match.

Design:
- One row per (keyword, message) — UNIQUE constraint dedupes if scan re-runs.
- keyword_scan_state tracks the highest message_id scanned per trigger so a
  new trigger backfills against history once, then only sees new messages.
  Semantic triggers use a "sem:" key prefix in the same table.
- Keyword matching goes through the FTS5 trigram index when available
  (queries >= 3 chars); shorter queries and FTS-less builds use LIKE, which is
  case-insensitive for ASCII.
- Notifications use osascript via subprocess; failure is silent (notification
  permission not granted is the common case and shouldn't block sync).
"""
from __future__ import annotations

import asyncio
import logging

import aiosqlite

from app.core import database
from app.core.config import Config, load_config
from app.core.database import DB_PATH

logger = logging.getLogger("app.triggers")

SEM_STATE_PREFIX = "sem:"
SEM_DISPLAY_PREFIX = "≈"
SEM_SCAN_CAP = 500  # newest messages per semantic trigger per scan — bounds embed cost
SEM_MIN_CONTENT_LEN = 6


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


def _citation(row) -> str:
    preview = (row["content"] or "")[:120]
    sender = row["sender_name"] or ""
    chat = row["chat_name"] or ""
    return f"{chat} · {sender}: {preview}" if chat else preview


async def _read_scan_state(db, key: str) -> int:
    rows = await db.execute_fetchall(
        "SELECT last_scanned_message_id FROM keyword_scan_state WHERE keyword = ?", (key,)
    )
    return int(rows[0]["last_scanned_message_id"]) if rows else 0


async def _write_scan_state(db, key: str, message_id: int) -> None:
    await db.execute(
        """INSERT INTO keyword_scan_state (keyword, last_scanned_message_id) VALUES (?, ?)
           ON CONFLICT(keyword) DO UPDATE SET last_scanned_message_id = excluded.last_scanned_message_id""",
        (key, message_id),
    )


async def _keyword_candidates(db, kw: str, since_id: int) -> list:
    base = "SELECT id, chat_name, sender_name, content FROM messages WHERE id > ?"
    if database.FTS_ENABLED and len(kw) >= 3:
        try:
            return await db.execute_fetchall(
                f"{base} AND id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?) ORDER BY id",
                (since_id, '"' + kw.replace('"', '""') + '"'),
            )
        except aiosqlite.OperationalError:
            pass
    return await db.execute_fetchall(
        f"{base} AND content LIKE ? ORDER BY id", (since_id, f"%{kw}%")
    )


async def _cleanup_orphan_state(db, cfg: Config) -> None:
    """Drop scan state for triggers removed from config (inbox history stays)."""
    valid = {kw.strip() for kw in cfg.keywords if kw.strip()}
    valid |= {SEM_STATE_PREFIX + t.query for t in cfg.semantic_triggers if t.query.strip()}
    rows = await db.execute_fetchall("SELECT keyword FROM keyword_scan_state")
    for (key,) in rows:
        if key not in valid:
            await db.execute("DELETE FROM keyword_scan_state WHERE keyword = ?", (key,))


async def _scan_keywords(db, cfg: Config, global_max: int) -> list[tuple[str, int, str]]:
    new_triggers: list[tuple[str, int, str]] = []
    for kw in cfg.keywords:
        kw = kw.strip()
        if not kw:
            continue
        since_id = await _read_scan_state(db, kw)
        for r in await _keyword_candidates(db, kw, since_id):
            cur = await db.execute(
                "INSERT OR IGNORE INTO keyword_triggers (keyword, message_id) VALUES (?, ?)",
                (kw, int(r["id"])),
            )
            if cur.rowcount > 0:
                new_triggers.append((kw, int(r["id"]), _citation(r)))
        await _write_scan_state(db, kw, global_max)
    return new_triggers


async def _scan_semantic(db, cfg: Config, global_max: int) -> list[tuple[str, int, str]]:
    """Embedding-based triggers. Message vectors are computed transiently for
    new rows only (never stored — 150k × 3KB is not a good trade)."""
    triggers = [t for t in cfg.semantic_triggers if t.query.strip()]
    if not triggers or not cfg.llm.embedding_model:
        return []
    from app.services.embedder import embed_batch, embed_text, rank_by_similarity, serialize_vec

    new_triggers: list[tuple[str, int, str]] = []
    for trig in triggers:
        state_key = SEM_STATE_PREFIX + trig.query
        since_id = await _read_scan_state(db, state_key)
        # Oldest-first: a backlog larger than the cap drains across scans instead
        # of scanning only the newest cap and skipping the middle. State advances
        # only over messages actually processed this scan.
        rows = await db.execute_fetchall(
            """SELECT id, chat_name, sender_name, content FROM messages
               WHERE id > ? AND LENGTH(content) >= ? ORDER BY id ASC LIMIT ?""",
            (since_id, SEM_MIN_CONTENT_LEN, SEM_SCAN_CAP),
        )
        if not rows:
            await _write_scan_state(db, state_key, global_max)
            continue
        capped = len(rows) == SEM_SCAN_CAP
        if capped:
            logger.info("semantic trigger %r has a backlog; scanning %d oldest this pass", trig.query, SEM_SCAN_CAP)

        query_vec = await embed_text(trig.query)
        if not query_vec:
            continue  # embed backend down — retry this trigger next scan
        contents = [r["content"] or "" for r in rows]
        vecs = await embed_batch(contents, cfg.llm.embedding_model)
        blobs, idx_map = [], []
        for i, v in enumerate(vecs):
            if v is not None:
                blobs.append(serialize_vec(v))
                idx_map.append(i)
        if not blobs:
            continue  # whole batch failed to embed — don't advance, retry next scan
        display = SEM_DISPLAY_PREFIX + trig.query
        for blob_idx, score in rank_by_similarity(query_vec, blobs, len(blobs)):
            if score < trig.threshold:
                break
            r = rows[idx_map[blob_idx]]
            cur = await db.execute(
                "INSERT OR IGNORE INTO keyword_triggers (keyword, message_id) VALUES (?, ?)",
                (display, int(r["id"])),
            )
            if cur.rowcount > 0:
                new_triggers.append((display, int(r["id"]), _citation(r)))
        # When capped, only advance to the batch's max id so the next scan
        # continues the backlog; otherwise everything up to global_max is seen.
        await _write_scan_state(db, state_key, int(rows[-1]["id"]) if capped else global_max)
    return new_triggers


async def scan_for_matches(*, notify: bool = True) -> int:
    """Run a full incremental trigger scan. Returns count of new triggers."""
    cfg = load_config()
    if not cfg.keywords and not cfg.semantic_triggers:
        return 0

    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        db.row_factory = aiosqlite.Row
        max_row = await db.execute_fetchall("SELECT COALESCE(MAX(id), 0) AS m FROM messages")
        global_max = int(max_row[0]["m"]) if max_row else 0
        if global_max == 0:
            return 0

        await _cleanup_orphan_state(db, cfg)
        new_triggers = await _scan_keywords(db, cfg, global_max)
        new_triggers += await _scan_semantic(db, cfg, global_max)
        await db.commit()

    # Cap notifications: if a new keyword backfills against history we'd spam
    # the user with hundreds. Notify on first 3, then a single "and N more".
    if notify and new_triggers:
        for kw, _, citation in new_triggers[:3]:
            await _osascript_notify(f"触发命中: {kw}", citation)
        if len(new_triggers) > 3:
            await _osascript_notify(
                "触发命中",
                f"还有 {len(new_triggers) - 3} 条匹配，到收件箱查看",
            )

    return len(new_triggers)


async def rescan_keyword(keyword: str) -> int:
    """Reset scan_state for a keyword and rescan from scratch. Used when the
    user wants historical matches for a freshly-edited rule."""
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        await db.execute("PRAGMA busy_timeout=30000")
        await db.execute("DELETE FROM keyword_scan_state WHERE keyword = ?", (keyword,))
        await db.execute("DELETE FROM keyword_triggers WHERE keyword = ?", (keyword,))
        await db.commit()
    return await scan_for_matches(notify=False)


async def notify_urgent_todos(min_urgency: int = 4) -> int:
    """Scheduled reminder: one notification summarizing open urgent todos."""
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT COUNT(*) AS n, MIN(summary) AS sample FROM analysis_results
               WHERE category = 'todo' AND done = 0 AND urgency >= ?""",
            (min_urgency,),
        )
    count = int(rows[0]["n"]) if rows else 0
    if count:
        sample = (rows[0]["sample"] or "")[:80]
        await _osascript_notify(
            f"待办提醒：{count} 条紧急待办未完成",
            sample or "到待办看板查看",
        )
    return count
