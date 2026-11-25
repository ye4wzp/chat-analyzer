"""Telegram sync via Telethon account API.

- Single TelegramClient kept alive in this process (StringSession persisted in config.json).
- Login is a 2-step interactive flow: start_login → confirm_code (+ optional 2FA password).
- Sync iterates dialogs and pulls messages with min_id incremental cursor.
"""

import asyncio
import base64
import io
import time
import uuid
from typing import Any, Awaitable, Callable

import aiosqlite
import segno
from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl.types import User

from app.core.config import load_config, save_config
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

ProgressCB = Callable[[int, str], Awaitable[None] | None]
PENDING_TTL = 600  # seconds before we drop a half-finished login

_client: TelegramClient | None = None
_pending: dict[str, dict[str, Any]] = {}  # phone -> {client, phone_code_hash, expires_at}
_pending_qr: dict[str, dict[str, Any]] = {}  # qr_id -> {client, qr, user?, password_needed?, error?, expires_at}


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


def _purge_expired_qr() -> None:
    now = time.time()
    for qid in [q for q, v in _pending_qr.items() if v["expires_at"] < now and not v.get("user")]:
        state = _pending_qr.pop(qid, None)
        if state:
            t = state.get("wait_task")
            if t and not t.done():
                t.cancel()
            try:
                asyncio.create_task(state["client"].disconnect())
            except Exception:
                pass


def _qr_png_data_url(url: str) -> str:
    """Render the tg://login token URL as a base64 PNG data URL the frontend can drop into <img>."""
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="png", scale=8, border=2)
    return f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}"


async def _wait_qr(qr_id: str) -> None:
    """Background task: blocks on QRLogin.wait() and parks the result in _pending_qr.

    Telethon raises SessionPasswordNeededError when 2FA is on — surface that as a
    `password_needed` flag so the frontend can prompt for the password and finish
    the sign-in via qr_password()."""
    state = _pending_qr.get(qr_id)
    if not state:
        return
    qr = state["qr"]
    try:
        user = await qr.wait()
        state["user"] = user
    except errors.SessionPasswordNeededError:
        state["password_needed"] = True
    except asyncio.TimeoutError:
        state["error"] = "QR expired"
    except asyncio.CancelledError:
        raise
    except Exception as e:
        state["error"] = str(e)


async def start_qr_login(api_id: int, api_hash: str) -> dict[str, Any]:
    """Issue a fresh tg://login token and start watching for the user's scan."""
    _purge_expired_qr()
    if not api_id or not api_hash:
        raise ValueError("api_id / api_hash 不能为空")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    qr = await client.qr_login()
    qr_id = uuid.uuid4().hex[:12]
    expires_at = qr.expires.timestamp() if qr.expires else time.time() + 30
    state: dict[str, Any] = {
        "client": client,
        "qr": qr,
        "api_id": api_id,
        "api_hash": api_hash,
        "expires_at": expires_at,
        "user": None,
        "password_needed": False,
        "error": None,
    }
    state["wait_task"] = asyncio.create_task(_wait_qr(qr_id))
    _pending_qr[qr_id] = state
    return {
        "qr_id": qr_id,
        "url": qr.url,
        "qr_png": _qr_png_data_url(qr.url),
        "expires_at": expires_at,
    }


async def qr_status(qr_id: str) -> dict[str, Any]:
    """Polled by the frontend every ~2s. Finalizes login on success."""
    state = _pending_qr.get(qr_id)
    if not state:
        return {"status": "expired"}
    if state.get("error"):
        err = state["error"]
        await _drop_qr(qr_id)
        return {"status": "error", "error": err}
    if state.get("password_needed"):
        return {"status": "password_needed"}
    user = state.get("user")
    if user:
        info = await _finalize_qr_login(qr_id, user)
        return {"status": "success", **info}
    if state["expires_at"] < time.time():
        await _drop_qr(qr_id)
        return {"status": "expired"}
    return {"status": "pending"}


async def qr_password(qr_id: str, password: str) -> dict[str, Any]:
    """Complete a QR login when the account has 2FA enabled."""
    state = _pending_qr.get(qr_id)
    if not state:
        raise RuntimeError("QR 会话已过期，请重新生成二维码")
    if not state.get("password_needed"):
        raise RuntimeError("当前会话不需要密码")
    client: TelegramClient = state["client"]
    try:
        user = await client.sign_in(password=password)
    except errors.PasswordHashInvalidError:
        raise RuntimeError("两步验证密码错误")
    if not isinstance(user, User):
        raise RuntimeError("登录失败：返回非用户对象")
    return await _finalize_qr_login(qr_id, user)


async def _finalize_qr_login(qr_id: str, user: User) -> dict[str, Any]:
    """Persist session, swap to active client, and remove the pending entry."""
    global _client
    state = _pending_qr.get(qr_id)
    if not state:
        raise RuntimeError("QR 会话不存在")
    client: TelegramClient = state["client"]

    cfg = load_config()
    cfg.telegram.api_id = state["api_id"]
    cfg.telegram.api_hash = state["api_hash"]
    cfg.telegram.session_string = client.session.save()
    cfg.telegram.username = user.username or (user.first_name or "")
    cfg.telegram.enabled = True
    save_config(cfg)

    if _client is not None and _client is not client:
        try:
            await _client.disconnect()
        except Exception:
            pass
    _client = client

    _pending_qr.pop(qr_id, None)
    return {
        "username": user.username or "",
        "first_name": user.first_name or "",
        "user_id": user.id,
    }


async def _drop_qr(qr_id: str) -> None:
    state = _pending_qr.pop(qr_id, None)
    if not state:
        return
    t = state.get("wait_task")
    if t and not t.done():
        t.cancel()
    try:
        await state["client"].disconnect()
    except Exception:
        pass


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


async def sync_all(progress: ProgressCB | None = None, *, on_result=None, chat_ids=None, skip_ids=()) -> dict:
    from app.services.sync.result import new_result, record_result, record_missing, error_reason
    client = await _ensure_client()
    if client is None:
        raise RuntimeError("Telegram 未登录")

    async def emit(pct, message):
        if progress:
            value = progress(pct, message)
            if asyncio.iscoroutine(value):
                await value

    cfg = load_config()
    result = new_result("telegram")
    dialogs = []
    async for d in client.iter_dialogs():
        cid = str(d.id)
        kind, name = _chat_kind(d.entity)
        if cid in skip_ids or (chat_ids is not None and cid not in chat_ids):
            continue
        if not in_sync_scope("telegram", cid, kind, cfg):
            result["excluded"] += 1
            continue
        dialogs.append((d, cid, kind, name))
    result["targets"] = len(dialogs)
    await record_missing(result, chat_ids, [d[1] for d in dialogs], on_result)
    account = current_account("telegram")
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        for i, (dialog, cid, kind, name) in enumerate(dialogs):
            await emit(int(95 * i / max(1, len(dialogs))), f"Telegram {i + 1}/{len(dialogs)}：{name}")
            last, _ = await read_sync_state(db, "telegram", cid)
            checkpoint = int(last) if last and last.isdigit() else 0
            latest = checkpoint
            inserted = committed = pending = 0
            try:
                # Ascending batches preserve a contiguous prefix even after cancellation.
                async for msg in client.iter_messages(dialog.entity, min_id=checkpoint, reverse=True):
                    if msg.action is None and (msg.message or msg.media):
                        sender = msg.sender
                        name_parts = [getattr(sender, "first_name", ""), getattr(sender, "last_name", "")]
                        sn = " ".join(filter(None, name_parts)).strip() or getattr(sender, "title", "") or getattr(sender, "username", "") or ""
                        ts = parse_ts(msg.date.timestamp() if msg.date else None)
                        if ts is None:
                            raise ValueError("InvalidMessageTimestamp")
                        text = msg.message or "[媒体]"
                        inserted += await upsert_message(db, platform="telegram", chat_id=cid, chat_name=name,
                            chat_type=kind, sender_id=str(getattr(msg, "sender_id", None) or getattr(sender, "id", "") or ""),
                            sender_name=sn, content=text, msg_type="text" if msg.message else "media", timestamp=ts,
                            src_id=source_id(msg.id, cid, ts, sn, text), account=account, cfg=cfg,
                            raw={"id": msg.id, "date": msg.date.isoformat(), "message": text,
                                 "reply_to_msg_id": getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None),
                                 "media_type": type(msg.media).__name__ if msg.media else None})
                    latest = max(latest, msg.id)
                    pending += 1
                    if pending >= 200:
                        await update_sync_state(db, platform="telegram", chat_id=cid, last_msg_id=str(latest))
                        await db.commit()
                        committed = inserted
                        pending = 0
                        await emit(int(95 * i / max(1, len(dialogs))), f"{name}：已保存 {committed} 条")
                await update_sync_state(db, platform="telegram", chat_id=cid, last_msg_id=str(latest))
                await db.commit()
                await record_result(result, cid, name, "done", inserted, on_result=on_result)
            except asyncio.CancelledError:
                await db.rollback()
                await record_result(result, cid, name, "pending", committed, error="Cancelled", on_result=on_result)
                raise
            except Exception as e:
                await db.rollback()
                await record_result(result, cid, name, "failed", committed, error=error_reason(e), on_result=on_result)
                if isinstance(e, errors.FloodWaitError):
                    result["retry_after"] = e.seconds
                    for _, remaining_id, _, remaining_name in dialogs[i + 1:]:
                        await record_result(result, remaining_id, remaining_name, "pending", error="FloodWait", on_result=on_result)
                    break
    return result
