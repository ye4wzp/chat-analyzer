"""Telegram sync via Telethon account API.

- Single TelegramClient kept alive in this process (StringSession persisted in config.json).
- Login is a 2-step interactive flow: start_login → confirm_code (+ optional 2FA password).
- Sync iterates dialogs and pulls messages with min_id incremental cursor.
"""

import asyncio
import time
from typing import Any, Awaitable, Callable

import aiosqlite
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import User

from app.core.config import load_config, save_config
from app.core.database import DB_PATH
from app.services.sync._common import (
    parse_ts,
    read_sync_state,
    source_id,
    update_sync_state,
    upsert_message,
)

ProgressCB = Callable[[int, str], Awaitable[None] | None]
PENDING_TTL = 600  # seconds before we drop a half-finished login

_client: TelegramClient | None = None
_pending: dict[str, dict[str, Any]] = {}  # phone -> {client, phone_code_hash, expires_at}


def _purge_expired() -> None:
    now = time.time()
    for phone in [p for p, v in _pending.items() if v["expires_at"] < now]:
        try:
            asyncio.create_task(_pending[phone]["client"].disconnect())
        except Exception:
            pass
        _pending.pop(phone, None)


async def start_login(api_id: int, api_hash: str, phone: str) -> dict[str, str]:
    _purge_expired()
    if not api_id or not api_hash or not phone:
        raise ValueError("api_id / api_hash / phone 不能为空")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    sent = await client.send_code_request(phone)
    _pending[phone] = {
        "client": client,
        "phone_code_hash": sent.phone_code_hash,
        "api_id": api_id,
        "api_hash": api_hash,
        "expires_at": time.time() + PENDING_TTL,
    }
    return {"phone_code_hash": sent.phone_code_hash}


async def confirm_code(
    phone: str, code: str, password: str | None = None
) -> dict[str, Any]:
    global _client
    state = _pending.get(phone)
    if not state:
        raise RuntimeError("登录会话已过期，请重新发送验证码")

    client: TelegramClient = state["client"]
    try:
        user = await client.sign_in(
            phone=phone, code=code, phone_code_hash=state["phone_code_hash"]
        )
    except errors.SessionPasswordNeededError:
        if not password:
            # keep state alive — frontend will re-call with password
            return {"need_password": True}
        user = await client.sign_in(password=password)
    except errors.PhoneCodeInvalidError:
        raise RuntimeError("验证码错误")
    except errors.PhoneCodeExpiredError:
        _pending.pop(phone, None)
        raise RuntimeError("验证码已过期，请重新发送")

    if not isinstance(user, User):
        raise RuntimeError("登录失败：返回非用户对象")

    cfg = load_config()
    cfg.telegram.api_id = state["api_id"]
    cfg.telegram.api_hash = state["api_hash"]
    cfg.telegram.phone = phone
    cfg.telegram.session_string = client.session.save()
    cfg.telegram.username = user.username or (user.first_name or "")
    cfg.telegram.enabled = True
    save_config(cfg)

    # promote to active client
    if _client is not None and _client is not client:
        try:
            await _client.disconnect()
        except Exception:
            pass
    _client = client
    _pending.pop(phone, None)

    return {
        "username": user.username or "",
        "first_name": user.first_name or "",
        "user_id": user.id,
    }


async def status() -> dict[str, Any]:
    cfg = load_config().telegram
    if not cfg.session_string or not cfg.api_id:
        return {"logged_in": False}
    client = await _ensure_client()
    if client is None:
        return {"logged_in": False}
    me = await client.get_me()
    return {
        "logged_in": True,
        "username": (me.username if me else cfg.username) or "",
        "first_name": (me.first_name if me else "") or "",
        "user_id": me.id if me else None,
    }


async def logout() -> None:
    global _client
    cfg = load_config()
    if _client is not None:
        try:
            await _client.log_out()
        except Exception:
            pass
        try:
            await _client.disconnect()
        except Exception:
            pass
        _client = None
    cfg.telegram.session_string = ""
    cfg.telegram.username = ""
    cfg.telegram.enabled = False
    save_config(cfg)


async def _ensure_client() -> TelegramClient | None:
    global _client
    if _client is not None and _client.is_connected():
        return _client
    cfg = load_config().telegram
    if not cfg.session_string or not cfg.api_id or not cfg.api_hash:
        return None
    client = TelegramClient(StringSession(cfg.session_string), cfg.api_id, cfg.api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        return None
    _client = client
    return _client


def _chat_kind(entity: Any) -> tuple[str, str]:
    """Return (chat_type, chat_name) — chat_type ∈ {private, group, channel}."""
    if getattr(entity, "broadcast", False):
        return "channel", getattr(entity, "title", "") or ""
    if getattr(entity, "megagroup", False) or hasattr(entity, "title"):
        return "group", getattr(entity, "title", "") or ""
    name = " ".join(filter(None, [
        getattr(entity, "first_name", "") or "",
        getattr(entity, "last_name", "") or "",
    ])).strip()
    return "private", name or (getattr(entity, "username", "") or "")


async def sync_all(progress: ProgressCB | None = None) -> dict[str, int]:
    client = await _ensure_client()
    if client is None:
        raise RuntimeError("Telegram 未登录")

    async def _emit(pct: int, msg: str) -> None:
        if progress is None:
            return
        rv = progress(pct, msg)
        if asyncio.iscoroutine(rv):
            await rv

    await _emit(10, "正在拉取对话列表...")
    dialogs = []
    async for d in client.iter_dialogs():
        dialogs.append(d)
    if not dialogs:
        await _emit(100, "无对话")
        return {"chats": 0, "imported": 0}

    total_imported = 0
    chats_done = 0

    async with aiosqlite.connect(str(DB_PATH)) as db:
        for i, dialog in enumerate(dialogs):
            chat_id = str(dialog.id)
            chat_type, chat_name = _chat_kind(dialog.entity)
            pct = 15 + int(80 * i / len(dialogs))
            await _emit(pct, f"同步: {chat_name or chat_id}")

            last_id, _ = await read_sync_state(db, "telegram", chat_id)
            min_id = int(last_id) if last_id and last_id.isdigit() else 0
            max_seen = min_id

            try:
                async for msg in client.iter_messages(dialog.entity, min_id=min_id):
                    if msg.action is not None:
                        continue
                    text = msg.message or ""
                    if not text and not msg.media:
                        continue
                    if not text:
                        text = "[媒体]"

                    sender = msg.sender
                    sender_name = ""
                    if sender:
                        if hasattr(sender, "first_name"):
                            sender_name = " ".join(filter(None, [
                                getattr(sender, "first_name", "") or "",
                                getattr(sender, "last_name", "") or "",
                            ])).strip() or (getattr(sender, "username", "") or "")
                        else:
                            sender_name = getattr(sender, "title", "") or ""

                    ts = parse_ts(msg.date.timestamp() if msg.date else None)
                    if not ts:
                        continue

                    raw = {
                        "id": msg.id,
                        "date": msg.date.isoformat() if msg.date else None,
                        "from_id": getattr(msg.from_id, "user_id", None) if msg.from_id else None,
                        "message": text,
                        "media_type": msg.media.__class__.__name__ if msg.media else None,
                    }
                    total_imported += await upsert_message(
                        db,
                        platform="telegram",
                        chat_id=chat_id,
                        chat_name=chat_name,
                        chat_type=chat_type,
                        sender_id=str(getattr(sender, "id", "") or ""),
                        sender_name=sender_name,
                        content=text,
                        msg_type="text" if msg.message else "media",
                        timestamp=ts,
                        src_id=source_id(msg.id, chat_id, ts, sender_name, text),
                        raw=raw,
                    )
                    if msg.id > max_seen:
                        max_seen = msg.id
            except errors.FloodWaitError as e:
                await _emit(pct, f"被限速，等待 {e.seconds} 秒...")
                await asyncio.sleep(e.seconds)
                continue
            except Exception:
                # network blips on a single dialog shouldn't kill the whole sync
                continue

            if max_seen > min_id:
                await update_sync_state(
                    db, platform="telegram", chat_id=chat_id, last_msg_id=str(max_seen)
                )
                await db.commit()
            chats_done += 1

    await _emit(100, f"完成，新增 {total_imported} 条消息（{chats_done} 个对话）")
    return {"chats": chats_done, "imported": total_imported}
