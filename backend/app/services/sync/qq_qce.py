"""QQ sync via NapCat-QCE (qq-chat-exporter) HTTP API.

QCE listens on http://{host}:{port} (default 40653) and exposes a Bearer-token
authed REST API. We hit it with httpx and shape results into our `messages` table.
"""

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import aiosqlite
import httpx

from app.core.config import QQConfig, load_config
from app.core.database import DB_PATH
from app.services.sync._common import (
    current_account,
    in_sync_scope,
    parse_ts,
    read_sync_state,
    source_id,
    update_sync_state,
    upsert_message,
)

CHAT_PRIVATE = 1
CHAT_GROUP = 2

# 单会话每轮最多翻的页数（200 条/页）。QCE 深度翻页到二十几页就会掉连接，卡在这个
# 上限之内可以少撞断连；没翻完的部分由 sync_state.backfill_page 记着，下轮接着翻。
PAGES_PER_RUN = 20

# 连接层的瞬时故障：重试有意义。HTTP 状态错误（403/500）不在此列——那是配置或
# 服务端的确定性问题，重试只是浪费，应该原样冒泡给调用方记成 skipped。
TRANSIENT_ERRORS = (
    httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError,
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout,
)
RETRY_DELAYS = (1.0, 3.0, 7.0)

# elementType → human marker for non-text elements. NTQQ values; we degrade gracefully.
ELEMENT_MARKERS = {
    2: "[图片]", 3: "[文件]", 4: "[语音]", 5: "[视频]",
    6: "[表情]", 7: "[引用回复]", 8: "[系统]", 11: "[表情包]", 16: "[卡片]",
}

ProgressCB = Callable[[int, str], Awaitable[None] | None]


def _unwrap(payload: Any) -> Any:
    """QCE v5 wraps responses as {success, data, timestamp, requestId}.
    Strip the envelope so call sites work with the inner data shape."""
    if isinstance(payload, dict) and "success" in payload and "data" in payload:
        if not payload.get("success", True):
            err = (payload.get("error") or {}).get("message") or "QCE 调用失败"
            raise QCEError(err)
        return payload["data"]
    return payload


class QCEError(RuntimeError):
    pass


class QCEClient:
    def __init__(self, cfg: QQConfig, timeout: float = 120.0):
        cfg = cfg.model_copy()
        # Only use this machine's credential for its managed loopback service.
        if cfg.host in ("localhost", "127.0.0.1") and cfg.port == 40653:
            from app.services.sync.qq_launcher import _token_from_disk
            cfg.token = _token_from_disk() or cfg.token
        if not cfg.token:
            raise QCEError("QCE token 未配置")
        self.cfg = cfg
        self.timeout = timeout
        self.base = f"http://{cfg.host}:{cfg.port}"
        self._client = self._new_client()
        self.cutoff = int(time.time() * 1000)

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base,
            trust_env=self.cfg.host not in ("localhost", "127.0.0.1", "::1"),
            headers={"Authorization": f"Bearer {self.cfg.token}"},
            timeout=self.timeout,
            # QCE silently closes idle keepalive sockets; reuse triggers
            # RemoteProtocolError. Disable the keepalive pool entirely.
            limits=httpx.Limits(max_keepalive_connections=0, max_connections=10),
        )

    async def reconnect(self) -> None:
        """Close the current connection pool and start a fresh one. Call this
        after an unexpected disconnect to avoid the dead pool poisoning later
        requests."""
        try:
            await self._client.aclose()
        except Exception:
            pass
        self._client = self._new_client()

    async def __aenter__(self) -> "QCEClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       body: dict | None = None) -> Any:
        """带退避的重试。断连后 QCE 需要几秒才恢复，立即重试只会连撞三次然后放弃
        （实测：深度翻页断连后紧接的一轮 5 秒内全败），所以逐次拉长间隔。"""
        for delay in (*RETRY_DELAYS, None):
            try:
                r = await self._client.request(method, path, params=params, json=body)
                r.raise_for_status()
                return _unwrap(r.json())
            except TRANSIENT_ERRORS as e:
                if delay is None:
                    raise
                logging.debug("QCE %s %s 断连(%s)，%.0fs 后重试", method, path, type(e).__name__, delay)
                await self.reconnect()
                await asyncio.sleep(delay)

    async def _get(self, path: str, **params: Any) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, body: dict) -> Any:
        return await self._request("POST", path, body=body)

    async def system_info(self) -> dict:
        return await self._get("/api/system/info")

    async def groups(self) -> list[dict]:
        data = await self._get("/api/groups", page=1, limit=999, forceRefresh="false")
        return data.get("groups", [])

    async def friends(self) -> list[dict]:
        data = await self._get("/api/friends", page=1, limit=999)
        return data.get("friends", [])

    async def fetch_messages(
        self, chat_type: int, peer_uid: str, *, page: int = 1, limit: int = 200
    ) -> dict:
        body = {
            "peer": {"chatType": chat_type, "peerUid": peer_uid},
            "batchSize": 5000,
            "page": page,
            "limit": limit,
            "filter": {"endTime": self.cutoff},
        }
        result = await self._post("/api/messages/fetch", body)
        result["hasNext"] = bool(result.get("hasNext")) or page * limit < int(result.get("totalCount") or 0)
        return result


def _extract_text(elements: list[dict]) -> str:
    """Concatenate textElement content; fall back to type markers for media."""
    parts: list[str] = []
    for elem in elements or []:
        et = elem.get("elementType")
        text_el = elem.get("textElement") or {}
        if et == 1 or text_el:
            parts.append(text_el.get("content", "") or "")
            continue
        marker = ELEMENT_MARKERS.get(et if isinstance(et, int) else -1)
        parts.append(marker or "")
    return "".join(parts).strip()


def _chat_meta(*, kind: str, peer: dict) -> tuple[str, str, str]:
    """Return (chat_id, chat_name, chat_type)."""
    if kind == "group":
        return str(peer.get("groupCode", "")), peer.get("groupName") or str(peer.get("groupCode", "")), "group"
    name = peer.get("remark") or peer.get("nick") or str(peer.get("uin") or peer.get("uid", ""))
    return str(peer.get("uid") or peer.get("uin", "")), name, "private"


async def test_connection(cfg: QQConfig) -> dict:
    async with QCEClient(cfg) as q:
        info = await q.system_info()
    # SystemInfo shape varies; surface common fields verbatim plus a digest.
    nick = info.get("selfNick") or info.get("self_nick") or info.get("nickname") or ""
    uin = info.get("selfUin") or info.get("self_uin") or info.get("uin") or ""
    return {"ok": True, "nick": nick, "uin": str(uin), "raw": info}


async def list_chats(cfg: QQConfig) -> dict:
    async with QCEClient(cfg) as q:
        groups, friends = await asyncio.gather(q.groups(), q.friends())
    return {"groups": groups, "friends": friends}


async def _read_backfill_page(db: aiosqlite.Connection, chat_id: str) -> int | None:
    cursor = await db.execute(
        "SELECT backfill_page FROM sync_state WHERE platform='qq' AND chat_id=?", (chat_id,)
    )
    row = await cursor.fetchone()
    return int(row[0]) if row and row[0] else None


async def _write_backfill_page(db: aiosqlite.Connection, chat_id: str, page: int | None) -> None:
    await db.execute(
        """INSERT INTO sync_state (platform, chat_id, backfill_page, updated_at)
           VALUES ('qq', ?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(platform, chat_id) DO UPDATE SET
             backfill_page=excluded.backfill_page, updated_at=CURRENT_TIMESTAMP""",
        (chat_id, page),
    )


class _Walk:
    """一次翻页遍历的结果。page 1 是最新，页码越大越旧。"""

    def __init__(self) -> None:
        self.inserted = 0
        self.max_seq: int | None = None
        self.done = False      # 撞到已同步的 seq / hasNext=False / 空页 —— 这一段翻完了
        self.last_page = 0     # 最后一个成功取回的页
        self.cut = False       # 被 QCE 断连截断


async def _walk_pages(
    db: aiosqlite.Connection,
    q: QCEClient,
    *,
    kind_meta: tuple[str, str, str],
    chat_type_code: int,
    peer_uid: str,
    start_page: int,
    stop_seq: int | None,
    max_pages: int,
) -> _Walk:
    """从 start_page 往更旧的方向翻，最多 max_pages 页。

    断连不抛出：QCE 在单会话深度翻页约二十几页后会掉连接（实测）。抛出会让本会话
    已取回的页连进度一起丢掉，下次又从 page 1 撞同一面墙，消息多的会话永远追不上。
    """
    account = current_account("qq")
    chat_id, chat_name, chat_type = kind_meta
    w = _Walk()
    page = start_page
    for _ in range(max_pages):
        try:
            result = await q.fetch_messages(chat_type_code, peer_uid, page=page, limit=200)
        except TRANSIENT_ERRORS as e:
            # 只吞连接层故障。403/500 这类是确定性问题，要冒泡出去记成 skipped，
            # 否则整批会话都会伪装成“旧历史没翻完”，把真故障藏起来。
            logging.info("QCE 断连于 %s 第 %d 页: %s", chat_name or chat_id, page, type(e).__name__)
            w.cut = True
            break

        messages = result.get("messages", [])
        if not messages:
            w.done = True
            break
        w.last_page = page

        for raw in messages:
            seq_str = str(raw.get("msgSeq", "")) or ""
            seq_int = int(seq_str) if seq_str.isdigit() else None
            if stop_seq is not None and seq_int is not None and seq_int <= stop_seq:
                w.done = True
                continue
            if seq_int is not None and (w.max_seq is None or seq_int > w.max_seq):
                w.max_seq = seq_int

            ts = parse_ts(raw.get("msgTime"))
            content = _extract_text(raw.get("elements", []))
            if not ts or not content:
                continue
            w.inserted += await upsert_message(
                db,
                platform="qq",
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=chat_type,
                sender_id=str(raw.get("senderUid") or raw.get("senderUin", "")),
                sender_name=raw.get("sendMemberName") or raw.get("sendNickName") or "",
                content=content,
                msg_type="text",
                timestamp=ts,
                src_id=source_id(raw.get("msgId"), chat_id, ts, raw.get("senderUid"), content),
                raw=raw,
                account=account,
            )

        if w.done or not result.get("hasNext"):
            w.done = True
            break
        page += 1
    return w


async def sync_one_chat(
    db: aiosqlite.Connection,
    q: QCEClient,
    *,
    kind: str,
    peer: dict,
) -> tuple[int, bool]:
    """返回 (入库条数, 是否仍有未取回的旧历史)。"""
    chat_id, chat_name, chat_type = _chat_meta(kind=kind, peer=peer)
    if not chat_id:
        return 0, False
    chat_type_code = CHAT_GROUP if kind == "group" else CHAT_PRIVATE
    peer_uid = str(peer.get("groupCode") if kind == "group" else (peer.get("uid") or peer.get("uin", "")))

    last_seq, _ = await read_sync_state(db, "qq", chat_id)
    last_seq_int = int(last_seq) if last_seq and last_seq.isdigit() else None
    resume = await _read_backfill_page(db, chat_id)
    saved = await db.execute_fetchall("SELECT backfill_cutoff FROM sync_state WHERE platform='qq' AND chat_id=?", (chat_id,))
    # Page offsets are only valid against the same bounded snapshot. Never use
    # an old page number against a newly moving history window.
    if resume and saved and saved[0][0]:
        q.cutoff = saved[0][0]
    else:
        q.cutoff = int(time.time() * 1000)
        if resume:
            resume = 1
    common = dict(
        kind_meta=(chat_id, chat_name, chat_type),
        chat_type_code=chat_type_code,
        peer_uid=peer_uid,
        max_pages=PAGES_PER_RUN,
    )

    # 1. 增量：page 1 起往旧翻，撞到上次的水位线即止。
    head = await _walk_pages(db, q, start_page=1, stop_seq=last_seq_int, **common)
    inserted = head.inserted

    # 从 page 1 连续翻下来，最新一条之上没有缺口，水位线可以推进；更旧的缺口交给
    # backfill_page 记录，不能因为水位线到了最新就当整段历史都齐了。
    if head.max_seq is not None:
        await update_sync_state(db, platform="qq", chat_id=chat_id, last_msg_id=str(head.max_seq))

    # head 中途断连（或 max_pages 用尽）时，断点必须落在它实际翻到的页之后，
    # 不能取旧的 resume：否则水位线已推进到最新，而 head.last_page..resume 之间
    # 那段消息既不会被下轮增量（撞新水位线即止）取回，也被 tail 跳过（从 resume
    # 起翻），造成永久缺口。head 走完（done）时才轮到旧 resume 继续回补。
    if not head.done:
        # last_page==0 表示第一页就断连、水位线未动（max_seq is None），
        # 保留旧 resume 不清零回补进度；否则以 head 实际停下的页为准。
        gap = (resume or 1) if head.last_page == 0 else head.last_page + 1
    else:
        gap = resume

    # 2. 回补：增量段已翻完时，接着上轮断掉的页继续往更旧翻。
    if head.done and resume:
        tail = await _walk_pages(db, q, start_page=resume, stop_seq=None, **common)
        inserted += tail.inserted
        gap = None if tail.done else max(resume, tail.last_page + 1)
        if tail.done:
            # Finish the old snapshot, then catch up the messages that arrived
            # during backfill before reporting this conversation complete.
            last_seq, _ = await read_sync_state(db, "qq", chat_id)
            q.cutoff = int(time.time() * 1000)
            fresh = await _walk_pages(db, q, start_page=1,
                                      stop_seq=int(last_seq) if last_seq and last_seq.isdigit() else None,
                                      **common)
            inserted += fresh.inserted
            if fresh.max_seq is not None:
                await update_sync_state(db, platform="qq", chat_id=chat_id, last_msg_id=str(fresh.max_seq))
            gap = None if fresh.done else fresh.last_page + 1

    await _write_backfill_page(db, chat_id, gap)
    await db.execute("UPDATE sync_state SET backfill_cutoff=? WHERE platform='qq' AND chat_id=?",
                     (q.cutoff if gap else None, chat_id))
    return inserted, gap is not None


async def sync_all(progress: ProgressCB | None = None, *, on_result=None, chat_ids=None, skip_ids=()) -> dict:
    from app.services.sync.result import new_result, record_result, record_missing, error_reason
    result = new_result("qq")
    cfg = load_config().qq
    if not cfg.enabled:
        raise QCEError("QQ 同步未启用")

    async def _emit(pct: int, msg: str) -> None:
        if progress is None:
            return
        rv = progress(pct, msg)
        if asyncio.iscoroutine(rv):
            await rv

    total = 0
    chats_done = 0
    skipped: list[str] = []
    pending: list[str] = []  # 仍有旧历史没翻完的会话，下轮继续
    async with QCEClient(cfg) as q:
        await _emit(10, "正在拉取群和好友列表...")
        groups, friends = await asyncio.gather(q.groups(), q.friends())
        targets: list[tuple[str, dict]] = [("group", g) for g in groups] + [("friend", f) for f in friends]
        if chat_ids is not None:
            targets = [(k, p) for k, p in targets if _chat_meta(kind=k, peer=p)[0] in chat_ids]
        scope_cfg = load_config()
        allowed = [(k, p) for k, p in targets
                   if _chat_meta(kind=k, peer=p)[0] not in skip_ids and in_sync_scope("qq", _chat_meta(kind=k, peer=p)[0], _chat_meta(kind=k, peer=p)[2], scope_cfg)]
        result["excluded"] = len(targets) - len(allowed)
        targets = allowed
        result["targets"] = len(targets)
        await record_missing(result, chat_ids, [_chat_meta(kind=k, peer=p)[0] for k, p in targets], on_result)
        if not targets:
            await _emit(100, "无聊天可同步")
            return {**result, "skipped": [], "pending": []}

        async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
            await db.execute("PRAGMA busy_timeout=30000")
            scope_cfg = load_config()
            for i, (kind, peer) in enumerate(targets):
                pct = 15 + int(80 * i / len(targets))
                cid, name, ctype = _chat_meta(kind=kind, peer=peer)
                if cid and not in_sync_scope("qq", cid, ctype, scope_cfg):
                    result["excluded"] += 1
                    result["targets"] -= 1
                    continue
                await _emit(pct, f"同步 {kind}: {name}")
                # 每个会话一个事务：失败的会话回滚掉自己的半截写入，不留给下一个
                # 会话的 commit 顺带提交（那样计数和检查点会对不上）。
                try:
                    got, partial = await sync_one_chat(db, q, kind=kind, peer=peer)
                except (httpx.HTTPError, QCEError) as e:
                    await db.rollback()
                    skipped.append(f"{name} ({type(e).__name__})")
                    logging.warning("QCE sync skipped %s %s: %s", kind, name, e)
                    # The connection pool may be in a bad state after a disconnect;
                    # rebuild it so the next chat doesn't inherit the failure.
                    await q.reconnect()
                    await record_result(result, cid, name, "failed", error=error_reason(e), on_result=on_result)
                    continue
                total += got
                chats_done += 1
                if partial:
                    pending.append(name or "?")
                await db.commit()
                await record_result(result, cid, name, "pending" if partial else "done", got, on_result=on_result)

    summary = f"完成，新增 {total} 条消息（{chats_done} 个聊天）"
    if pending:
        summary += f"，{len(pending)} 个会话旧历史未翻完（下轮继续）"
    if skipped:
        summary += f"，跳过 {len(skipped)} 个"
    await _emit(100, summary)
    return {**result, "skipped": skipped, "pending": pending}
