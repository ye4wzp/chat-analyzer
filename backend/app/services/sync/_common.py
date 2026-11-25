"""Shared helpers for platform sync services (qq_qce, telegram_live, ...)."""

import json
import hashlib

import aiosqlite

from app.core.config import load_config
from app.core.time_utils import normalize_timestamp


def in_sync_scope(platform: str, chat_id: str, chat_type: str | None, cfg=None) -> bool:
    """Ingest gate: excluded chats and (by default) broadcast channels never
    enter the database. Cheaper than filtering every view later."""
    from app.services.sync.result import WECHAT_FOLD_GROUP
    if platform == "wechat" and chat_id == WECHAT_FOLD_GROUP:
        return False
    cfg = cfg or load_config()
    scope = cfg.sync_scope
    if scope.exclude_channels and chat_type == "channel":
        return False
    return not any(r.platform == platform and r.chat_id == chat_id for r in scope.exclude_chats)


def current_account(platform: str) -> str:
    """Label of the user's own account on `platform`, stamped onto new rows so
    the 多号 switch can tell accounts apart. '' = unlabeled/default account."""
    cfg = load_config()
    if platform == "wechat":
        return cfg.wechat.account.strip()
    if platform == "qq":
        return cfg.qq.uin.strip()
    if platform == "telegram":
        return (cfg.telegram.username or "").strip()
    return ""


def parse_ts(value: str | int | float | None) -> str | None:
    """Normalize a timestamp from any plausible source into ISO 8601."""
    return normalize_timestamp(value)


def source_id(raw: object, *parts: object) -> str:
    """Use the platform's own message id when present, else sha1 of fingerprint."""
    if raw not in (None, "", 0):
        return str(raw)
    fingerprint = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()


def local_tz_offset_minutes() -> int:
    """Minutes east of UTC for the machine right now (stored per message)."""
    from datetime import datetime
    off = datetime.now().astimezone().utcoffset()
    return int(off.total_seconds() // 60) if off else 0


def keep_raw(platform: str, kind: str, cfg=None) -> bool:
    """raw_data is insurance for re-parsing. For plain text on WeChat/Telegram
    the raw JSON repeats content/sender/time — 4× the size of the text itself —
    so it's dropped unless storage.keep_raw_text is on. QQ raw carries element
    structure we can't reconstruct, so it is always kept."""
    cfg = cfg or load_config()
    if cfg.storage.keep_raw_text or platform == "qq":
        return True
    return kind != "text"


async def upsert_message(
    db: aiosqlite.Connection,
    *,
    platform: str,
    chat_id: str,
    chat_name: str,
    chat_type: str,
    sender_id: str,
    sender_name: str,
    content: str,
    msg_type: str,
    timestamp: str,
    src_id: str,
    raw: object,
    account: str = "",
    cfg=None,
) -> int:
    """Insert one message; rows already present are ignored. Returns 0 or 1.

    This is the single ingest point: kind is classified here so every consumer
    (pre-filter, stats, search) reads one vocabulary; tz offset is stamped so
    hour-of-day stats survive travelling; raw_data follows the storage policy."""
    from app.services.stats import classify_kind

    kind = classify_kind(platform, msg_type, content)
    raw_json = json.dumps(raw, ensure_ascii=False, default=str) if keep_raw(platform, kind, cfg) else None
    cursor = await db.execute(
        """INSERT OR IGNORE INTO messages
           (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
            content, msg_type, kind, timestamp, tz_offset, source_id, raw_data, account)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            platform, chat_id, chat_name, chat_type,
            sender_id, sender_name, content, msg_type, kind, timestamp, local_tz_offset_minutes(),
            src_id, raw_json, account or None,
        ),
    )
    return max(cursor.rowcount, 0)


async def update_sync_state(
    db: aiosqlite.Connection,
    *,
    platform: str,
    chat_id: str,
    last_timestamp: str | None = None,
    last_msg_id: str | None = None,
) -> None:
    await db.execute(
        """INSERT INTO sync_state (platform, chat_id, last_msg_id, last_timestamp, updated_at)
           VALUES (?,?,?,?,CURRENT_TIMESTAMP)
           ON CONFLICT(platform, chat_id) DO UPDATE SET
             last_msg_id=COALESCE(excluded.last_msg_id, sync_state.last_msg_id),
             last_timestamp=COALESCE(excluded.last_timestamp, sync_state.last_timestamp),
             updated_at=CURRENT_TIMESTAMP""",
        (platform, chat_id, last_msg_id, last_timestamp),
    )


async def read_sync_state(
    db: aiosqlite.Connection, platform: str, chat_id: str,
) -> tuple[str | None, str | None]:
    """Return (last_msg_id, last_timestamp)."""
    cursor = await db.execute(
        "SELECT last_msg_id, last_timestamp FROM sync_state WHERE platform=? AND chat_id=?",
        (platform, chat_id),
    )
    row = await cursor.fetchone()
    return (row[0], row[1]) if row else (None, None)
