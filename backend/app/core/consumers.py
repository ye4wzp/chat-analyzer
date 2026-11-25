"""Message-log consumers.

Everything derived from messages (stats rollups, kind backfill, ...) follows the
same shape: read rows with id > cursor in batches, process, advance the cursor,
commit. `run_consumer` is that loop; each consumer only supplies the columns it
needs and a batch handler. Cursors live in the `consumers` table.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import aiosqlite

BatchHandler = Callable[[aiosqlite.Connection, list[aiosqlite.Row]], Awaitable[None]]
Progress = Callable[[int, str], None] | None


async def get_cursor(db: aiosqlite.Connection, name: str) -> int:
    rows = await db.execute_fetchall("SELECT last_message_id FROM consumers WHERE name = ?", (name,))
    return int(rows[0][0]) if rows else 0


async def set_cursor(db: aiosqlite.Connection, name: str, value: int) -> None:
    await db.execute(
        """INSERT INTO consumers (name, last_message_id, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(name) DO UPDATE SET last_message_id = excluded.last_message_id, updated_at = CURRENT_TIMESTAMP""",
        (name, value),
    )


async def max_message_id(db: aiosqlite.Connection) -> int:
    rows = await db.execute_fetchall("SELECT COALESCE(MAX(id), 0) FROM messages")
    return int(rows[0][0]) if rows else 0


async def run_consumer(
    db: aiosqlite.Connection,
    name: str,
    columns: str,
    handler: BatchHandler,
    *,
    batch_size: int = 20_000,
    progress: Progress = None,
    label: str = "处理",
    start: int | None = None,
) -> tuple[int, int]:
    """Drain messages past the consumer's cursor. Returns (processed, cursor).
    `start` overrides the stored cursor (0 = replay everything)."""
    cursor = await get_cursor(db, name) if start is None else start
    max_id = await max_message_id(db)
    span = max(1, max_id - cursor)
    origin = cursor
    processed = 0
    while cursor < max_id:
        rows = await db.execute_fetchall(
            f"SELECT {columns} FROM messages WHERE id > ? ORDER BY id LIMIT ?",
            (cursor, batch_size),
        )
        if not rows:
            break
        await handler(db, rows)
        cursor = int(rows[-1]["id"])
        await set_cursor(db, name, cursor)
        await db.commit()
        processed += len(rows)
        if progress:
            pct = 2 + int(96 * min(1.0, (cursor - origin) / span))
            progress(pct, f"已{label} {processed:,} 条消息...")
    if cursor != await get_cursor(db, name):
        await set_cursor(db, name, cursor)
        await db.commit()
    return processed, cursor
