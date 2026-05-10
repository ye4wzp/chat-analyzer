"""QQ sync via NapCat-QCE (qq-chat-exporter) HTTP API.

QCE listens on http://{host}:{port} (default 40653) and exposes a Bearer-token
authed REST API. We hit it with httpx and shape results into our `messages` table.
"""

import asyncio
from typing import Any, Awaitable, Callable

import aiosqlite
import httpx

from app.core.config import QQConfig, load_config
from app.core.database import DB_PATH
from app.services.sync._common import (
    parse_ts,
    read_sync_state,
    source_id,
    update_sync_state,
    upsert_message,
)

CHAT_PRIVATE = 1
CHAT_GROUP = 2

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
    def __init__(self, cfg: QQConfig, timeout: float = 30.0):
        if not cfg.token:
            raise QCEError("QCE token 未配置")
        self.base = f"http://{cfg.host}:{cfg.port}"
        self._client = httpx.AsyncClient(
            base_url=self.base,
            headers={"Authorization": f"Bearer {cfg.token}"},
            timeout=timeout,
        )

    async def __aenter__(self) -> "QCEClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params: Any) -> Any:
        r = await self._client.get(path, params=params)
        r.raise_for_status()
        return _unwrap(r.json())

    async def _post(self, path: str, body: dict) -> Any:
        r = await self._client.post(path, json=body)
        r.raise_for_status()
        return _unwrap(r.json())

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
        }
        return await self._post("/api/messages/fetch", body)


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


async def sync_one_chat(
    db: aiosqlite.Connection,
    q: QCEClient,
    *,
    kind: str,
    peer: dict,
) -> int:
    chat_id, chat_name, chat_type = _chat_meta(kind=kind, peer=peer)
    if not chat_id:
        return 0
    chat_type_code = CHAT_GROUP if kind == "group" else CHAT_PRIVATE
    peer_uid = str(peer.get("groupCode") if kind == "group" else (peer.get("uid") or peer.get("uin", "")))

    last_seq, _ = await read_sync_state(db, "qq", chat_id)
    last_seq_int = int(last_seq) if last_seq and last_seq.isdigit() else None

    inserted = 0
    page = 1
    max_seq_seen: int | None = None
    stop = False
    while not stop:
        result = await q.fetch_messages(chat_type_code, peer_uid, page=page, limit=200)
        messages = result.get("messages", [])
        if not messages:
            break

        for raw in messages:
            seq_str = str(raw.get("msgSeq", "")) or ""
            seq_int = int(seq_str) if seq_str.isdigit() else None
            if last_seq_int is not None and seq_int is not None and seq_int <= last_seq_int:
                stop = True
                continue
            if seq_int is not None and (max_seq_seen is None or seq_int > max_seq_seen):
                max_seq_seen = seq_int

            ts = parse_ts(raw.get("msgTime"))
            content = _extract_text(raw.get("elements", []))
            if not ts or not content:
                continue
            inserted += await upsert_message(
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
            )

        if not result.get("hasNext"):
            break
        page += 1

    if max_seq_seen is not None:
        await update_sync_state(db, platform="qq", chat_id=chat_id, last_msg_id=str(max_seq_seen))
    return inserted


async def sync_all(progress: ProgressCB | None = None) -> dict:
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
    async with QCEClient(cfg) as q:
        await _emit(10, "正在拉取群和好友列表...")
        groups, friends = await asyncio.gather(q.groups(), q.friends())
        targets: list[tuple[str, dict]] = [("group", g) for g in groups] + [("friend", f) for f in friends]
        if not targets:
            await _emit(100, "无聊天可同步")
            return {"chats": 0, "imported": 0}

        async with aiosqlite.connect(str(DB_PATH)) as db:
            for i, (kind, peer) in enumerate(targets):
                pct = 15 + int(80 * i / len(targets))
                _, name, _ = _chat_meta(kind=kind, peer=peer)
                await _emit(pct, f"同步 {kind}: {name}")
                try:
                    total += await sync_one_chat(db, q, kind=kind, peer=peer)
                    chats_done += 1
                except httpx.HTTPError:
                    continue
                await db.commit()

    await _emit(100, f"完成，新增 {total} 条消息（{chats_done} 个聊天）")
    return {"chats": chats_done, "imported": total}
