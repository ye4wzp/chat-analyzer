"""Read an existing qq-cli plaintext snapshot without modifying the QQ source."""
import inspect
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.core import database
from app.services.sync._common import in_sync_scope, parse_ts, upsert_message
from app.services.sync.result import error_reason, new_result, record_missing, record_result
from app.vendor.qqcli.segment import parse_blob, render_inline

TABLES = {"group": "group_msg_table", "private": "c2c_msg_table"}
PAGE_SIZE = 256
LOOKBACK_SECONDS = 86400


class LocalSourceError(ValueError):
    pass


def source_path(cfg):
    default = Path(os.environ.get("QQCLI_HOME") or "~/.qqmac") / "plain"
    path = Path(cfg.local_path or os.environ.get("QQCLI_PLAIN_DIR") or default).expanduser()
    if path.is_dir() or (not cfg.local_path and path.suffix != ".db"):
        path = path / "nt_msg.db"
    return path.resolve()


@asynccontextmanager
async def open_source(cfg):
    path = source_path(cfg)
    if not cfg.local_account.strip():
        raise LocalSourceError("请填写本地数据库所属的 QQ 账号，以便分别记录同步进度")
    if not path.is_file():
        raise LocalSourceError(f"找不到可读的 QQ 消息数据库：{path}")
    try:
        with path.open("rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise LocalSourceError("此文件不是可读的 SQLite 消息库，请选择 qq-cli 的明文 nt_msg.db 副本")
    except OSError as e:
        raise LocalSourceError(f"无法打开 QQ 消息库：{e}") from e
    async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as src:
        try:
            await src.execute("PRAGMA query_only=ON")
            await src.execute("BEGIN")
            for table in TABLES.values():
                await src.execute(f"SELECT [40001],[40050],[40020],[40090],[40021],[40030],[40800] FROM {table} LIMIT 1")
        except sqlite3.Error as e:
            raise LocalSourceError(f"QQ 消息库无法读取私聊或群聊表：{e}") from e
        yield src, path


async def test_source(cfg):
    try:
        async with open_source(cfg):
            return {"ok": True, "nick": "本地消息库可读", "uin": cfg.local_account}
    except sqlite3.Error as e:
        raise LocalSourceError(f"无法打开本地消息库：{e}") from e


async def lookup(src, query):
    try:
        rows = await src.execute_fetchall(query)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e) or "no such column" in str(e):
            return {}
        raise
    return {str(k): str(v) for k, v in rows if k and v}


async def side_lookup(path, query):
    if not path.is_file():
        return {}
    async with aiosqlite.connect(path.as_uri() + "?mode=ro", uri=True) as side:
        return await lookup(side, query)


async def sync_local(cfg, *, progress=None, on_result=None, chat_ids=None, skip_ids=(), mode=None):
    if not cfg.qq.enabled:
        raise LocalSourceError("QQ 同步未启用")
    account = cfg.qq.local_account.strip()
    result = new_result("qq")
    async with open_source(cfg.qq) as (src, path), aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        groups = await side_lookup(path.with_name("group_info.db"), "SELECT [60001],[60007] FROM group_list")
        profile = path.with_name("profile_info.db")
        names = await side_lookup(profile, "SELECT [1002],[20002] FROM profile_info_v6")
        uids = await lookup(src, "SELECT [1002],[48902] FROM nt_uid_mapping_table")
        uids.update(await side_lookup(profile, "SELECT [1002],[1000] FROM buddy_list"))
        sender_names = {uid: names[qq] for qq, uid in uids.items() if qq in names}
        saved = {(kind, peer): (cid, ts, mid) for kind, peer, cid, ts, mid in await db.execute_fetchall(
            "SELECT chat_type,peer,chat_id,ts,msg_id FROM qq_local_cursor WHERE account=?", (account,))}
        targets = []
        excluded_ids = set()
        requested = set(chat_ids) if chat_ids is not None else None
        for kind, table in TABLES.items():
            peers = await src.execute_fetchall(f"SELECT DISTINCT [40030] FROM {table} WHERE [40030]!=0 AND [40050]>0")
            for (peer,) in peers:
                number = str(peer)
                checkpoint = saved.get((kind, number))
                cid = number if kind == "group" else uids.get(number) or f"private:{number}"
                if checkpoint:
                    cid = checkpoint[0]
                if cid in skip_ids or (requested is not None and cid not in requested):
                    continue
                aliases = {cid} if kind == "group" else {cid, uids.get(number, cid)}
                if any(not in_sync_scope("qq", alias, kind, cfg) for alias in aliases):
                    result["excluded"] += 1
                    excluded_ids.add(cid)
                    continue
                name = (groups if kind == "group" else names).get(number, number)
                targets.append((kind, peer, cid, name, checkpoint))
        result["targets"] = len(targets)
        # Explicit retries may include a chat blacklisted since the first run.
        for cid in sorted((requested or set()) & excluded_ids):
            await record_result(result, cid, cid, "excluded")
        await record_missing(result, None if requested is None else requested - excluded_ids - set(skip_ids),
                             [t[2] for t in targets], on_result)
        for index, (kind, peer, cid, name, checkpoint) in enumerate(targets):
            if progress:
                reply = progress(5 + 90 * index // len(targets), f"读取 QQ {'群聊' if kind == 'group' else '私聊'} · {name}")
                if inspect.isawaitable(reply):
                    await reply
            # First run and repair scan all available history. A later incremental
            # scan replays the tail to include messages downloaded out of order.
            ranges = [((0, 0), None)]
            if checkpoint and mode not in ("full", "repair"):
                high = checkpoint[1:]
                floor = max(0, high[0] - LOOKBACK_SECONDS - 1)
                # Replay first. On failure the previously saved high-water mark
                # still covers the same lookback window on the next attempt.
                ranges = [((floor, 0), high), (high, None)]
            inserted = 0
            try:
                for start, end in ranges:
                    async for count in import_pages(src, db, cfg, account, kind, peer, cid, name, start, end, sender_names):
                        inserted += count
            except (ValueError, sqlite3.Error, OverflowError) as e:
                await db.rollback()
                await record_result(result, cid, name, "failed", inserted, error_reason(e), on_result)
                continue
            await record_result(result, cid, name, "done", inserted, on_result=on_result)
    return result


async def import_pages(src, db, cfg, account, kind, peer, cid, name, start, end, names):
    table = TABLES[kind]
    # Start strictly before the first timestamp so even negative signed QQ IDs fit.
    ts, mid = start
    upper_bound = "AND ([40050],[40001])<=(?,?)" if end else ""
    # One ordered scan per range, even for a source without a composite index.
    async with src.execute(
        f"""SELECT [40050],[40001],[40020],[40090],[40800] FROM {table}
            WHERE [40030]=? AND [40050]>0 AND ([40050],[40001])>(?,?) {upper_bound}
            ORDER BY [40050],[40001]""", (peer, ts, mid, *(end or ())),
    ) as cursor:
        while rows := await cursor.fetchmany(PAGE_SIZE):
            ids = [str(row[1]) for row in rows]
            keys = ids + [f"local:{account}:{key}" for key in ids]
            placeholders = ",".join("?" for _ in keys)
            await db.execute("BEGIN IMMEDIATE")
            owners = dict(await db.execute_fetchall(
                f"SELECT source_id,account FROM messages WHERE platform='qq' AND chat_id=? AND source_id IN ({placeholders})",
                (cid, *keys)))
            count = 0
            for ts, mid, sender, card, blob in rows:
                if not isinstance(mid, int):
                    raise LocalSourceError("消息 ID 格式异常，无法记录同步位置")
                timestamp = parse_ts(ts)
                if not timestamp:
                    raise LocalSourceError(f"消息 {mid} 的时间无法解析")
                src_id = str(mid)
                # Preserve native IDs for QCE interoperability. With two known
                # owners, namespace the second row to fit the legacy unique index.
                if src_id in owners and owners[src_id] != account:
                    src_id = f"local:{account}:{mid}"
                if src_id in owners:
                    continue
                segments = parse_blob(blob)
                text = render_inline(segments) or "[未知消息]"
                count += await upsert_message(db, platform="qq", chat_id=cid, chat_name=name, chat_type=kind,
                                              sender_id=str(sender or ""), sender_name=card or names.get(sender) or str(sender or ""),
                                              content=text, msg_type=segments[0].kind if segments else "unknown",
                                              timestamp=timestamp, src_id=src_id, account=account, cfg=cfg,
                                              raw={"msgId": str(mid), "segments": [s.to_dict() for s in segments]})
            await db.execute("""INSERT INTO qq_local_cursor(account,chat_type,peer,chat_id,ts,msg_id)
                                VALUES(?,?,?,?,?,?) ON CONFLICT(account,chat_type,peer) DO UPDATE SET
                                ts=excluded.ts,msg_id=excluded.msg_id WHERE
                                (excluded.ts,excluded.msg_id)>(qq_local_cursor.ts,qq_local_cursor.msg_id)""",
                             (account, kind, str(peer), cid, ts, mid))
            await db.execute("""INSERT INTO sync_state(platform,chat_id,last_timestamp,updated_at)
                                VALUES('qq',?,?,CURRENT_TIMESTAMP) ON CONFLICT(platform,chat_id) DO UPDATE SET
                                last_timestamp=MAX(COALESCE(sync_state.last_timestamp,''),excluded.last_timestamp),
                                updated_at=CURRENT_TIMESTAMP""", (cid, timestamp))
            await db.commit()
            yield count
