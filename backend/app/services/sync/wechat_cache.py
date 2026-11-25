"""Repair from wx-cli's existing cache manifest, without reading keys or decrypting."""
import asyncio
import ctypes
import ctypes.util
import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import aiosqlite

from app.core import database
from app.services.sync._common import in_sync_scope, parse_ts, upsert_message
from app.services.sync.result import record_result, error_reason

WX_HOME = Path.home() / ".wx-cli"
_zstd = None


def decode_body(value, flag):
    global _zstd
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    raw = bytes(value)
    if raw.startswith(b"\x28\xb5\x2f\xfd"):
        if _zstd is None:
            path = ctypes.util.find_library("zstd")
            if not path:
                raise RuntimeError("ZstandardUnavailable")
            _zstd = ctypes.CDLL(path)
            _zstd.ZSTD_getFrameContentSize.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            _zstd.ZSTD_getFrameContentSize.restype = ctypes.c_ulonglong
            _zstd.ZSTD_decompress.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            _zstd.ZSTD_decompress.restype = ctypes.c_size_t
            _zstd.ZSTD_isError.argtypes = [ctypes.c_size_t]
            _zstd.ZSTD_isError.restype = ctypes.c_uint
        source = ctypes.create_string_buffer(raw)
        size = _zstd.ZSTD_getFrameContentSize(source, len(raw))
        size = 16 * 1024 * 1024 if size >= 2**64 - 2 else size
        if size > 16 * 1024 * 1024:
            raise ValueError("MessageTooLarge")
        dest = ctypes.create_string_buffer(max(1, size))
        n = _zstd.ZSTD_decompress(dest, size, source, len(raw))
        if _zstd.ZSTD_isError(n):
            raise ValueError("InvalidCompressedMessage")
        return dest.raw[:n].decode("utf-8")
    if flag == 4:
        raise ValueError("UnsupportedCompression")
    return raw.decode("utf-8")


def cache_sources():
    root = (WX_HOME / "cache").resolve()
    manifest = root / "_mtimes.json"
    if not manifest.exists():
        return []
    sources = []
    for key, item in json.loads(manifest.read_text()).items():
        if not re.fullmatch(r"message/(?:biz_)?message_\d+\.db", key):
            continue
        path = Path(item["path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("InvalidCacheManifest")
        sources.append(path)
    return list(dict.fromkeys(sources))


async def repair_cache(result, cfg, *, chat_ids=None, on_result=None, skip_ids=(), expected_cache=(), optional=False):
    sources = cache_sources()
    if not sources:
        if optional:
            return
        # A missing source is not evidence that there are no older messages.
        await record_result(result, "@cache", "本机历史缓存", "pending",
                            error="CacheUnavailable", on_result=on_result)
        return
    original = {d["chat_id"]: d for d in result["details"]}
    connections = []
    tables_by_chat = defaultdict(list)
    try:
        for path in sources:
            src = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            connections.append(src)
            src.row_factory = sqlite3.Row
            src.execute("PRAGMA query_only=ON")
            src.execute("BEGIN")
            tables = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")}
            mapped = set()
            for (cid,) in src.execute("SELECT user_name FROM Name2Id"):
                table = "Msg_" + hashlib.md5(cid.encode()).hexdigest()
                if table not in tables or table in mapped:
                    continue
                mapped.add(table)
                kind = "group" if cid.endswith("@chatroom") else "private"
                if cid not in skip_ids and (chat_ids is None or cid in chat_ids) and in_sync_scope("wechat", cid, kind, cfg):
                    tables_by_chat[cid].append((src, table))
            if mapped != tables:
                raise ValueError("UnmappedCacheTables")
        required = set(expected_cache) - set(skip_ids)
        if chat_ids is not None:
            required.intersection_update(chat_ids)
        cache_targets = set(tables_by_chat) | required
        result["cache_targets"] = sorted(cache_targets)
        result["target_ids"] = sorted(set(result.get("target_ids", original)) | cache_targets)
        result["targets"] = len(result["target_ids"])
        if on_result:
            import inspect
            saved = on_result(result)
            if inspect.isawaitable(saved):
                await saved
        for cid in sorted(required - set(tables_by_chat)):
            old = original.get(cid)
            if old:
                result["details"].remove(old)
                result["imported"] -= old["imported"]
                result["chats"] -= int(old["status"] == "done")
            await record_result(result, cid, old["name"] if old else cid, "pending",
                                old["imported"] if old else 0, error="之前记录的缓存表当前不可用",
                                live_done=bool(old and old["status"] == "done"), on_result=on_result)
        async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
            for cid, sources_for_chat in tables_by_chat.items():
                old = original.get(cid)
                name = old["name"] if old else cid
                got = 0
                try:
                    known = {}
                    for sid, raw, stamp in await db.execute_fetchall(
                        "SELECT source_id,raw_data,timestamp FROM messages WHERE platform='wechat' AND chat_id=?", (cid,)
                    ):
                        if sid and str(sid).isdigit():
                            known[int(sid)] = stamp
                        if raw:
                            obj = json.loads(raw)
                            if isinstance(obj, dict) and str(obj.get("local_id", "")).isdigit():
                                known[int(obj["local_id"])] = stamp
                    seen = 0
                    for src, table in sources_for_chat:
                        sql = f'''SELECT m.local_id,m.local_type,m.create_time,m.message_content,
                                         m.WCDB_CT_message_content,n.user_name
                                  FROM "{table}" m LEFT JOIN Name2Id n ON n.rowid=m.real_sender_id'''
                        for row in src.execute(sql):
                            stamp = parse_ts(row["create_time"])
                            if stamp is None:
                                raise ValueError("InvalidTimestamp")
                            lid = int(row["local_id"])
                            if lid in known:
                                if known[lid] != stamp:
                                    raise ValueError("CacheIdentityConflict")
                                continue
                            body = decode_body(row["message_content"], row["WCDB_CT_message_content"])
                            typ = int(row["local_type"]) & 0xffffffff
                            label = {1: "文本", 3: "图片", 34: "语音", 43: "视频", 47: "表情", 48: "位置", 49: "链接/文件", 10000: "系统"}.get(typ, str(typ))
                            sender = row["user_name"] or ""
                            got += await upsert_message(db, platform="wechat", chat_id=cid, chat_name=name,
                                chat_type="group" if cid.endswith("@chatroom") else "private", sender_id=sender,
                                sender_name=sender, content=body or ("[图片]" if typ == 3 else ""), msg_type=label,
                                timestamp=stamp, src_id=str(lid), raw={"local_id": lid, "cache_table": table, "content": body},
                                account=cfg.wechat.account, cfg=cfg)
                            known[lid] = stamp
                            seen += 1
                            if seen % 500 == 0:
                                await asyncio.sleep(0)
                    # Only a successful live read may advance the live watermark.
                    await db.commit()
                    if old:
                        result["details"].remove(old)
                        result["chats"] -= int(old["status"] == "done")
                        result["imported"] -= old["imported"]
                    # Cache coverage does not prove that a failing live source
                    # is current. Preserve that distinction for retry and UI.
                    status = "done" if old and old["status"] == "done" else "pending"
                    await record_result(result, cid, name, status, got + (old["imported"] if old else 0),
                                        error=None if status == "done" else "仅本机缓存可读，实时读取未确认：" + ((old or {}).get("error") or "会话未列出"), on_result=on_result, cache_checked=True)
                except Exception as e:
                    await db.rollback()
                    if old:
                        result["details"].remove(old)
                        result["chats"] -= int(old["status"] == "done")
                        result["imported"] -= old["imported"]
                    await record_result(result, cid, name, "failed", old["imported"] if old else 0,
                                        error=error_reason(e), on_result=on_result, live_done=bool(old and old["status"] == "done"))
    finally:
        for src in connections:
            src.close()
