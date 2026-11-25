"""Pre-aggregated message statistics.

The messages table is millions of rows; every chart on the overview / calendar /
member pages would otherwise be a full GROUP BY. This module keeps small rollup
tables (schema in core/database.py) in sync by walking messages.id past the
last processed id — INSERT OR IGNORE keeps ids monotonic, so "id > cursor" is a
complete incremental cursor. A full rebuild wipes the rollups and replays from 0.

Also extracts "resources" (links / files / mini-programs / red packets /
transfers) into their own table so the resources page can filter cheaply.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import defaultdict
from datetime import datetime

import aiosqlite

from app.core import consumers, database

logger = logging.getLogger("app.stats")

# Bump when classify_kind / extraction rules change so old rollups get rebuilt.
STATS_VERSION = "7"
CONSUMER = "stats"          # cursor name in the consumers table
KIND_CONSUMER = "kind_backfill"
BATCH_SIZE = 20_000
CONTENT_PEEK = 600  # chars of content fetched per row for classification / URL extraction

# Cross-platform message kinds. Order here is the display order on the UI.
KINDS: tuple[str, ...] = (
    "text", "image", "quote", "system", "sticker", "miniprogram", "link", "share",
    "file", "video", "voice", "call", "location", "card", "redpacket", "transfer",
    "media", "other",
)

KIND_LABEL: dict[str, str] = {
    "text": "文本", "image": "图片", "quote": "引用", "system": "系统", "sticker": "表情",
    "miniprogram": "小程序", "link": "链接", "share": "分享", "file": "文件", "video": "视频",
    "voice": "语音", "call": "通话", "location": "位置", "card": "名片", "redpacket": "红包",
    "transfer": "转账", "media": "媒体", "other": "其他",
}

_PREFIX_RULES: tuple[tuple[str, str], ...] = (
    ("[引用回复]", "quote"),
    ("[引用]", "quote"),
    ("[小程序]", "miniprogram"),
    ("[转账]", "transfer"),
    ("[红包]", "redpacket"),
    ("[链接]", "link"),
    ("[分享]", "share"),
    ("[图片]", "image"),
    ("[视频]", "video"),
    ("[语音]", "voice"),
    ("[表情包]", "sticker"),
    ("[表情]", "sticker"),
    ("[动画表情]", "sticker"),
    ("[系统]", "system"),
    ("[文件]", "file"),
    ("[媒体]", "media"),
    ("[位置]", "location"),
    ("[名片]", "card"),
    ("[通话]", "call"),
    ("[语音通话]", "call"),
    ("[视频通话]", "call"),
)

_TYPE_MAP: dict[str, str] = {
    # wechat (wx-cli labels)
    "文本": "text", "图片": "image", "链接/文件": "link", "链接": "link", "文件": "file",
    "系统": "system", "视频": "video", "表情": "sticker", "语音": "voice", "通话": "call",
    "位置": "location", "名片": "card", "引用": "quote", "小程序": "miniprogram",
    "红包": "redpacket", "转账": "transfer",
    # telegram / generic english
    "text": "text", "media": "media", "sticker": "sticker", "video": "video", "photo": "image",
    "image": "image", "document": "file", "file": "file", "voice": "voice", "audio": "voice",
    "animation": "sticker", "system": "system", "link": "link", "location": "location",
    "contact": "card", "call": "call",
}

_URL_RE = re.compile(r"https?://[^\s<>\"'\]\)）】》，。；！？]+", re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_FILE_EXT_RE = re.compile(
    r"([\w\-.() \u4e00-\u9fff]{1,120}\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|md|csv|json|"
    r"apk|dmg|exe|mp[34]|mov|jpe?g|png|gif|epub|key|numbers|pages|ipa|tar|gz))\b",
    re.IGNORECASE,
)


def classify_kind(platform: str, msg_type: str | None, content: str | None) -> str:
    """Map (platform label, wx/qq/tg msg_type, content) to one cross-platform kind.

    Content prefixes win over the platform type: wx-cli lumps quotes, links,
    mini-programs and payments under the single type "链接/文件", and QQ exports
    stamp everything as "text" with a `[图片]`-style marker in the body.
    """
    c = (content or "").lstrip()
    if c:
        # Payment placeholders are WeChat-specific; a Telegram channel post that
        # merely *talks about* 微信红包 must stay text.
        is_wx = platform == "wechat"
        if c.startswith("[链接] <![CDATA[微信红包]]>") or (is_wx and "微信红包" in c[:40]):
            return "redpacket"
        if c.startswith("[链接] <![CDATA[微信转账]]>") or (is_wx and "微信转账" in c[:40]) or c.startswith("[转账]"):
            return "transfer"
        for prefix, kind in _PREFIX_RULES:
            if c.startswith(prefix):
                return kind
        # WeChat "pat" notices and unsupported-content placeholders arrive typed
        # as 链接/文件 — they're system noise, not links.
        head = c[:60]
        if "拍了拍" in head and head.startswith('"'):
            return "system"
        if "不支持展示该内容" in head:
            return "system"
    t = (msg_type or "").strip()
    if t in _TYPE_MAP:
        return _TYPE_MAP[t]
    if t.startswith("type="):
        return "other"
    if not t:
        return "text" if c else "other"
    return "other"


def extract_resources(kind: str, content: str | None) -> list[tuple[str, str | None, str | None]]:
    """Return [(resource_kind, url, title)] found in one message."""
    c = (content or "").strip()
    if not c:
        return []
    out: list[tuple[str, str | None, str | None]] = []

    if kind in ("redpacket", "transfer"):
        m = _CDATA_RE.search(c)
        title = m.group(1).strip() if m else c[:80]
        return [(kind, None, title)]

    if kind == "miniprogram":
        title = c[len("[小程序]"):].strip() if c.startswith("[小程序]") else c
        title = _CDATA_RE.sub(lambda m: m.group(1), title).strip()
        return [("miniprogram", None, title[:200] or None)]

    if kind == "file":
        body = c[len("[文件]"):].strip() if c.startswith("[文件]") else c
        body = _CDATA_RE.sub(lambda m: m.group(1), body).strip()
        return [("file", None, body[:200] or None)]

    urls = _URL_RE.findall(c)
    seen: set[str] = set()
    if urls:
        # Title = the message text with URLs stripped, if anything is left.
        stripped = _URL_RE.sub("", c)
        stripped = _CDATA_RE.sub(lambda m: m.group(1), stripped)
        stripped = re.sub(r"^\[(链接|分享|引用|引用回复)\]\s*", "", stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        title = stripped[:200] or None
        for u in urls[:3]:
            u = u.rstrip(".,;:!?、")
            if u in seen:
                continue
            seen.add(u)
            out.append(("link", u, title))
        return out

    if kind in ("link", "share"):
        body = re.sub(r"^\[(链接|分享)\]\s*", "", c)
        body = _CDATA_RE.sub(lambda m: m.group(1), body).strip()
        if body:
            fm = _FILE_EXT_RE.search(body)
            if fm and len(body) < 160:
                return [("file", None, body[:200])]
            return [("link", None, body[:200])]
    return out


def _sender_key(sender_id: str | None, sender_name: str | None) -> str:
    sid = (sender_id or "").strip()
    if sid:
        return sid
    return (sender_name or "").strip() or "?"


class _Agg:
    """In-memory accumulators for one batch."""

    def __init__(self) -> None:
        self.daily: dict[tuple, int] = defaultdict(int)
        self.hourly: dict[tuple, int] = defaultdict(int)
        self.sender: dict[tuple, list] = {}       # key -> [n, name, first, last]
        self.sender_hourly: dict[tuple, int] = defaultdict(int)
        self.chats: dict[tuple, list] = {}        # key -> [name, type, n, first, last]
        self.resources: list[tuple] = []

    def add(self, row: aiosqlite.Row) -> None:
        ts = row["timestamp"] or ""
        if len(ts) < 13 or ts[4] != "-" or ts[10] not in ("T", " "):
            return
        day, month = ts[:10], ts[:7]
        try:
            hour = int(ts[11:13])
        except ValueError:
            return
        if not 0 <= hour < 24:
            return

        account = row["account"] or ""
        platform = row["platform"]
        chat_id = row["chat_id"]
        content = row["content"]
        kind = row["kind"] or classify_kind(platform, row["msg_type"], content)

        self.daily[(account, platform, chat_id, day, kind)] += 1
        self.hourly[(account, platform, chat_id, hour)] += 1

        ckey = (account, platform, chat_id)
        c = self.chats.get(ckey)
        if c is None:
            self.chats[ckey] = [row["chat_name"], row["chat_type"], 1, ts, ts]
        else:
            c[2] += 1
            if ts < c[3]:
                c[3] = ts
            if ts >= c[4]:
                c[4] = ts
                if row["chat_name"]:
                    c[0] = row["chat_name"]
                if row["chat_type"]:
                    c[1] = row["chat_type"]

        skey = _sender_key(row["sender_id"], row["sender_name"])
        sk = (account, platform, chat_id, skey, month)
        s = self.sender.get(sk)
        name = (row["sender_name"] or "").strip() or None
        if s is None:
            self.sender[sk] = [1, name, ts, ts]
        else:
            s[0] += 1
            if ts < s[2]:
                s[2] = ts
            if ts >= s[3]:
                s[3] = ts
                if name:
                    s[1] = name
        self.sender_hourly[(account, platform, chat_id, skey, hour)] += 1

        if kind in ("link", "share", "file", "miniprogram", "redpacket", "transfer") or (
            content and "http" in content
        ):
            for rkind, url, title in extract_resources(kind, content):
                self.resources.append((
                    row["id"], account, platform, chat_id, row["chat_name"], row["sender_name"],
                    ts, rkind, url, title,
                ))

    async def flush(self, db: aiosqlite.Connection) -> None:
        await db.executemany(
            """INSERT INTO stats_daily (account, platform, chat_id, day, kind, n) VALUES (?,?,?,?,?,?)
               ON CONFLICT(account, platform, chat_id, day, kind) DO UPDATE SET n = n + excluded.n""",
            [(*k, n) for k, n in self.daily.items()],
        )
        await db.executemany(
            """INSERT INTO stats_hourly (account, platform, chat_id, hour, n) VALUES (?,?,?,?,?)
               ON CONFLICT(account, platform, chat_id, hour) DO UPDATE SET n = n + excluded.n""",
            [(*k, n) for k, n in self.hourly.items()],
        )
        await db.executemany(
            """INSERT INTO stats_chats (account, platform, chat_id, chat_name, chat_type, n, first_ts, last_ts)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(account, platform, chat_id) DO UPDATE SET
                 n = n + excluded.n,
                 first_ts = MIN(COALESCE(first_ts, excluded.first_ts), excluded.first_ts),
                 chat_name = CASE WHEN excluded.last_ts >= COALESCE(last_ts, '') AND excluded.chat_name IS NOT NULL
                                  THEN excluded.chat_name ELSE chat_name END,
                 chat_type = CASE WHEN excluded.last_ts >= COALESCE(last_ts, '') AND excluded.chat_type IS NOT NULL
                                  THEN excluded.chat_type ELSE chat_type END,
                 last_ts = MAX(COALESCE(last_ts, excluded.last_ts), excluded.last_ts)""",
            [(*k, v[0], v[1], v[2], v[3], v[4]) for k, v in self.chats.items()],
        )
        await db.executemany(
            """INSERT INTO stats_sender (account, platform, chat_id, sender_key, month, n, sender_name, first_ts, last_ts)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account, platform, chat_id, sender_key, month) DO UPDATE SET
                 n = n + excluded.n,
                 sender_name = CASE WHEN excluded.last_ts >= COALESCE(last_ts, '') AND excluded.sender_name IS NOT NULL
                                    THEN excluded.sender_name ELSE COALESCE(sender_name, excluded.sender_name) END,
                 first_ts = MIN(COALESCE(first_ts, excluded.first_ts), excluded.first_ts),
                 last_ts = MAX(COALESCE(last_ts, excluded.last_ts), excluded.last_ts)""",
            [(*k, v[0], v[1], v[2], v[3]) for k, v in self.sender.items()],
        )
        await db.executemany(
            """INSERT INTO stats_sender_hourly (account, platform, chat_id, sender_key, hour, n) VALUES (?,?,?,?,?,?)
               ON CONFLICT(account, platform, chat_id, sender_key, hour) DO UPDATE SET n = n + excluded.n""",
            [(*k, n) for k, n in self.sender_hourly.items()],
        )
        if self.resources:
            await db.executemany(
                """INSERT OR IGNORE INTO resources
                   (message_id, account, platform, chat_id, chat_name, sender_name, timestamp, kind, url, title)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                self.resources,
            )


_self_cache: dict = {"at": 0.0, "value": {}}
SELF_CACHE_TTL = 120.0


async def infer_self_identities(db: aiosqlite.Connection) -> dict[str, dict]:
    """Guess which sender is the user on each platform: in private chats the
    one sender that shows up across the most distinct chats is "me". Returns
    {platform: {"sender_key", "sender_name", "chats"}}. Cached briefly."""
    import time

    now = time.monotonic()
    if _self_cache["value"] and now - _self_cache["at"] < SELF_CACHE_TTL:
        return _self_cache["value"]
    rows = await db.execute_fetchall(
        """SELECT s.platform, s.sender_key, MAX(s.sender_name) AS sender_name,
                  COUNT(DISTINCT s.chat_id) AS chats, SUM(s.n) AS n
           FROM stats_sender s
           JOIN stats_chats c ON c.platform = s.platform AND c.chat_id = s.chat_id AND c.account = s.account
           WHERE c.chat_type = 'private' AND s.sender_key <> '?'
           GROUP BY s.platform, s.sender_key
           HAVING chats >= 2
           ORDER BY chats DESC"""
    )
    out: dict[str, dict] = {}
    for r in rows:
        if r["platform"] not in out:
            out[r["platform"]] = {
                "sender_key": r["sender_key"], "sender_name": r["sender_name"],
                "chats": int(r["chats"]), "messages": int(r["n"]),
            }
    _self_cache["at"] = now
    _self_cache["value"] = out
    return out


async def _get_state(db: aiosqlite.Connection) -> dict[str, str]:
    rows = await db.execute_fetchall("SELECT key, value FROM stats_state")
    return {r[0]: r[1] for r in rows}


async def _set_state(db: aiosqlite.Connection, **kv: object) -> None:
    await db.executemany(
        "INSERT INTO stats_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(k, str(v)) for k, v in kv.items()],
    )


async def _clear(db: aiosqlite.Connection) -> None:
    for table in ("stats_daily", "stats_hourly", "stats_chats", "stats_sender", "stats_sender_hourly", "resources"):
        await db.execute(f"DELETE FROM {table}")
    await consumers.set_cursor(db, CONSUMER, 0)
    await _set_state(db, version=STATS_VERSION)


async def _cursor(db: aiosqlite.Connection, state: dict[str, str]) -> int:
    """Stats cursor from the consumers table; falls back to (and migrates) the
    legacy stats_state.last_message_id key."""
    cur = await consumers.get_cursor(db, CONSUMER)
    legacy = int(state.get("last_message_id", 0) or 0)
    if cur == 0 and legacy:
        await consumers.set_cursor(db, CONSUMER, legacy)
        await db.execute("DELETE FROM stats_state WHERE key = 'last_message_id'")
        return legacy
    return cur


async def get_status() -> dict:
    async with database.connect_db() as db:
        state = await _get_state(db)
        last = await _cursor(db, state)
        max_id = await consumers.max_message_id(db)
        kind_cursor = await consumers.get_cursor(db, KIND_CONSUMER)
    version_ok = state.get("version") == STATS_VERSION
    # Cursor ahead of the newest message = rows were deleted or an older
    # backup was restored; the incremental walk can't undo counts, so the
    # rollups are stale until a full rebuild.
    stale = last > max_id
    return {
        "ready": version_ok and "built_at" in state and not stale,
        "version_ok": version_ok,
        "stale": stale,
        "last_message_id": last,
        "max_message_id": max_id,
        "behind": max(0, max_id - last) if version_ok else max_id,
        "built_at": state.get("built_at"),
        "kind_backfill_behind": max(0, max_id - kind_cursor),
    }


def rebuild_reason(state: dict[str, str], cursor: int, max_id: int) -> str | None:
    """Why a full rebuild is needed, or None if incremental is fine."""
    if state.get("version") != STATS_VERSION:
        return "统计规则升级" if state.get("version") else "首次构建"
    if cursor > max_id:
        return "检测到消息被删除或从备份恢复"
    return None


_refresh_lock: "asyncio.Lock | None" = None


def _lock() -> "asyncio.Lock":
    global _refresh_lock
    if _refresh_lock is None:
        _refresh_lock = asyncio.Lock()
    return _refresh_lock


async def refresh(progress=None, *, full: bool = False) -> dict:
    """Bring rollups up to date. `full` (or a version mismatch) wipes and replays.
    Serialized with a lock: two concurrent walkers would double-count a batch."""
    async with _lock():
        return await _refresh_locked(progress, full=full)


_ROW_COLUMNS = (
    "id, account, platform, chat_id, chat_name, chat_type, sender_id, sender_name, "
    f"substr(content, 1, {CONTENT_PEEK}) AS content, msg_type, kind, timestamp"
)


async def _refresh_locked(progress, *, full: bool) -> dict:
    def emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    async with database.connect_db() as db:
        state = await _get_state(db)
        cursor = await _cursor(db, state)
        max_id = await consumers.max_message_id(db)
        reason = "手动重建" if full else rebuild_reason(state, cursor, max_id)
        start = None
        if reason:
            logger.info("stats full rebuild: %s", reason)
            emit(1, f"{reason}，正在全量重建统计...")
            await _clear(db)
            await db.commit()
            start = 0

        async def handle(conn: aiosqlite.Connection, rows: list[aiosqlite.Row]) -> None:
            agg = _Agg()
            for r in rows:
                agg.add(r)
            await agg.flush(conn)

        processed, cursor = await consumers.run_consumer(
            db, CONSUMER, _ROW_COLUMNS, handle, batch_size=BATCH_SIZE, progress=emit, label="统计", start=start,
        )
        await _set_state(db, version=STATS_VERSION, built_at=datetime.now().isoformat(timespec="seconds"))
        await db.commit()

    emit(100, f"统计完成，本次处理 {processed:,} 条")
    return {"processed": processed, "last_message_id": cursor}


async def backfill_kinds(progress=None) -> dict:
    """Fill messages.kind for rows ingested before the column existed. Runs as
    its own consumer so it can be resumed; new rows get kind at insert time."""
    def emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    async def handle(conn: aiosqlite.Connection, rows: list[aiosqlite.Row]) -> None:
        updates = [
            (classify_kind(r["platform"], r["msg_type"], r["content"]), r["id"])
            for r in rows if r["kind"] is None
        ]
        if updates:
            await conn.executemany("UPDATE messages SET kind = ? WHERE id = ?", updates)

    async with database.connect_db() as db:
        processed, cursor = await consumers.run_consumer(
            db, KIND_CONSUMER,
            f"id, platform, msg_type, kind, substr(content, 1, {CONTENT_PEEK}) AS content",
            handle, batch_size=BATCH_SIZE, progress=emit, label="归一",
        )
    emit(100, f"消息类型归一完成，处理 {processed:,} 条")
    return {"processed": processed, "last_message_id": cursor}


def spawn_refresh_if_needed(*, full: bool = False) -> str | None:
    """Fire a background stats task unless one is already running."""
    from app.core.runners import run_stats_refresh
    from app.core.tasks import create_task, is_task_running, spawn_cancellable

    if is_task_running("stats"):
        return None
    task_id = create_task("stats")
    spawn_cancellable(task_id, run_stats_refresh(task_id, full=full))
    return task_id


async def refresh_on_startup() -> None:
    """Kick off an incremental refresh (or a rebuild on version change) after
    the app boots, so the first overview request has data."""
    try:
        status = await get_status()
    except Exception as e:
        logger.warning("stats status check failed: %s", e)
        return
    if not status["version_ok"] or status["stale"] or status["behind"] > 0 or not status["ready"] or status["kind_backfill_behind"] > 0:
        why = ("规则升级" if not status["version_ok"] else "数据回退" if status["stale"]
               else f"落后 {status['behind']} 条" if status["behind"] else f"类型归一待补 {status['kind_backfill_behind']} 条")
        logger.info("stats refresh on startup (%s)", why)
        spawn_refresh_if_needed()
