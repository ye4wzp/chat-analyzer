"""SQLite backup using VACUUM INTO + gzip.

Why: `chat.db` is the only persistent store (15万+ messages, knowledge, sync state
tied together). A single bad write or crash mid-sync wipes everything. We snapshot
before each sync and let the user restore manually (gunzip + cp) if needed.

VACUUM INTO is the safe online-backup primitive — it won't tear during concurrent
WAL writers. We gzip the output because the DB is ~240MB raw but compresses to
~40MB, so 5 backups stay under 250MB instead of 1.2GB.
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

    # 1. Snapshot to raw_path. VACUUM INTO doesn't accept parameter binding for
    #    the path, so we escape single quotes and inline.
    escaped = str(raw_path).replace("'", "''")
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        await db.execute(f"VACUUM INTO '{escaped}'")

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
