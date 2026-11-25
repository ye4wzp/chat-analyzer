"""SQLite backup: asset tables only, gzipped.

Why: `chat.db` mixes the one thing that can't be regenerated (messages, plus
user decisions: knowledge, tags, persons, todos, reports) with derived data
(stats rollups, extracted resources, FTS index) that is ~30% of the file and
rebuilds itself on startup. Backups copy only the asset tables into a fresh
database — schema taken verbatim from sqlite_master, rows via INSERT…SELECT
inside one read transaction (a consistent WAL snapshot) — then gzip.

Restore: stop the server, `gunzip -c <backup> > data/chat.db`, start. Missing
derived tables are recreated and rebuilt automatically.
"""
from __future__ import annotations

import asyncio
import gzip
import shutil
import time
from datetime import datetime
from pathlib import Path

import aiosqlite

from app.core.config import BASE_DIR
from app.core.database import DB_PATH

BACKUPS_DIR = BASE_DIR / "backups"
KEEP_BACKUPS = 5
SUFFIX = ".bak.gz"


def _format_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{unit}" if unit != "B" else f"{n}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


async def create_backup() -> dict:
    """Snapshot chat.db via VACUUM INTO, gzip it, prune old backups.

    Returns metadata about the created backup (name, size, created_at)."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    raw_path = BACKUPS_DIR / f"chat.db.{ts}"
    final_path = BACKUPS_DIR / f"chat.db.{ts}{SUFFIX}"

    # 1. Copy asset tables into a fresh database (off the event loop: the
    #    messages copy is the bulk of the work and takes tens of seconds).
    await asyncio.to_thread(copy_assets, DB_PATH, raw_path)

    # 2. Gzip the snapshot off the event loop — 240MB compress takes a few seconds
    #    and would block other API calls if run sync.
    def _compress() -> None:
        with raw_path.open("rb") as src, gzip.open(final_path, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
        raw_path.unlink()

    await asyncio.to_thread(_compress)

    # 3. Prune old backups. Sort by name (which contains ISO timestamp) so newest last.
    prune_old_backups(KEEP_BACKUPS)

    stat = final_path.stat()
    return {
        "name": final_path.name,
        "size": stat.st_size,
        "size_human": _format_size(stat.st_size),
        "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


# Tables that are rebuilt from messages on startup and therefore not backed up.
DERIVED_TABLES = {
    "stats_daily", "stats_hourly", "stats_chats", "stats_sender", "stats_sender_hourly",
    "resources", "stats_state", "consumers",
}


def _is_derived(name: str) -> bool:
    return name in DERIVED_TABLES or name.startswith("messages_fts") or name.startswith("sqlite_")


def copy_assets(src: Path, dest: Path) -> dict:
    """Create `dest` containing only asset tables + their indexes. Synchronous;
    call via asyncio.to_thread. Returns per-table row counts."""
    import sqlite3

    if dest.exists():
        dest.unlink()
    con = sqlite3.connect(str(src), timeout=60)
    try:
        con.execute("PRAGMA busy_timeout=60000")
        escaped = str(dest).replace("'", "''")
        con.execute(f"ATTACH DATABASE '{escaped}' AS bak")
        con.execute("BEGIN")  # one read snapshot for every table below
        objects = con.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_master
               WHERE sql IS NOT NULL AND type IN ('table', 'index')
               ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, rowid"""
        ).fetchall()
        counts: dict[str, int] = {}
        for typ, name, tbl, sql in objects:
            if _is_derived(tbl) or _is_derived(name):
                continue
            # Re-point the DDL at the attached schema: "CREATE TABLE x" → "CREATE TABLE bak.x".
            ddl = sql.replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ", 1)
            ddl = ddl.replace("CREATE TABLE ", "CREATE TABLE bak.", 1) if typ == "table" else \
                ddl.replace("CREATE INDEX IF NOT EXISTS ", "CREATE INDEX ", 1) \
                   .replace("CREATE UNIQUE INDEX IF NOT EXISTS ", "CREATE UNIQUE INDEX ", 1) \
                   .replace("CREATE INDEX ", "CREATE INDEX bak.", 1) \
                   .replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX bak.", 1)
            con.execute(ddl)
            if typ == "table":
                con.execute(f'INSERT INTO bak."{name}" SELECT * FROM main."{name}"')
                counts[name] = con.execute(f'SELECT COUNT(*) FROM bak."{name}"').fetchone()[0]
        con.execute("COMMIT")
        con.execute("DETACH DATABASE bak")
        return counts
    finally:
        con.close()


def list_backups() -> list[dict]:
    if not BACKUPS_DIR.exists():
        return []
    items = []
    for p in sorted(BACKUPS_DIR.glob(f"chat.db.*{SUFFIX}"), reverse=True):
        stat = p.stat()
        items.append({
            "name": p.name,
            "size": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        })
    return items


def prune_old_backups(keep: int = KEEP_BACKUPS) -> int:
    """Delete oldest backups beyond `keep`. Returns number deleted."""
    if not BACKUPS_DIR.exists():
        return 0
    backups = sorted(BACKUPS_DIR.glob(f"chat.db.*{SUFFIX}"), reverse=True)
    deleted = 0
    for p in backups[keep:]:
        try:
            p.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def delete_backup(name: str) -> bool:
    """Delete a specific backup by name. Validates that the name is inside the
    backups dir to prevent path traversal."""
    if "/" in name or ".." in name:
        return False
    target = BACKUPS_DIR / name
    if not target.exists() or target.parent != BACKUPS_DIR:
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


# Throttle: don't create backups closer than this many seconds apart.
# Avoids flooding the disk when /sync/all fires 3 syncs back-to-back.
_MIN_INTERVAL = 300  # 5 min
_last_backup_at: float = 0.0


async def maybe_backup_before_sync() -> dict | None:
    """Best-effort pre-sync hook. Throttled to one backup per 5 min so /sync/all
    doesn't trigger 3 in a row. Failures are swallowed — sync should run even if
    backup fails (e.g. disk full)."""
    global _last_backup_at
    now = time.time()
    if now - _last_backup_at < _MIN_INTERVAL:
        return None
    try:
        info = await create_backup()
        _last_backup_at = now
        return info
    except Exception:
        return None
