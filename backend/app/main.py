import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.core.config import load_config, save_config, Config
from app.core.database import init_db, DB_PATH

FRONTEND_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"

app = FastAPI(title="Chat Analyzer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory task tracking
_tasks: dict[str, dict] = {}
_pending_results: dict[str, list[dict]] = {}  # task_id -> analysis results pending review


@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(_scheduler_loop())


def _is_task_running(task_type: str) -> bool:
    return any(t["type"] == task_type and t["status"] == "running" for t in _tasks.values())


async def _scheduler_loop():
    while True:
        await asyncio.sleep(30)
        try:
            cfg = load_config()
            s = cfg.scheduler
            now = datetime.utcnow()

            if s.sync_enabled and s.sync_interval_minutes > 0 and not _is_task_running("sync_wechat"):
                last = datetime.fromisoformat(s.last_sync_at) if s.last_sync_at else None
                if not last or now >= last + timedelta(minutes=s.sync_interval_minutes):
                    task_id = _create_task("sync_wechat")
                    asyncio.create_task(_run_sync(task_id, True))
                    cfg.scheduler.last_sync_at = now.isoformat()
                    save_config(cfg)

            if s.qq_enabled and s.qq_interval_minutes > 0 and not _is_task_running("sync_qq"):
                last = datetime.fromisoformat(s.last_qq_sync_at) if s.last_qq_sync_at else None
                if not last or now >= last + timedelta(minutes=s.qq_interval_minutes):
                    task_id = _create_task("sync_qq")
                    asyncio.create_task(_run_qq_sync(task_id))
                    cfg = load_config()
                    cfg.scheduler.last_qq_sync_at = now.isoformat()
                    save_config(cfg)

            if s.telegram_enabled and s.telegram_interval_minutes > 0 and not _is_task_running("sync_telegram"):
                last = datetime.fromisoformat(s.last_telegram_sync_at) if s.last_telegram_sync_at else None
                if not last or now >= last + timedelta(minutes=s.telegram_interval_minutes):
                    task_id = _create_task("sync_telegram")
                    asyncio.create_task(_run_telegram_sync(task_id))
                    cfg = load_config()
                    cfg.scheduler.last_telegram_sync_at = now.isoformat()
                    save_config(cfg)

            if s.analyze_enabled and s.analyze_interval_minutes > 0 and not _is_task_running("analyze"):
                last = datetime.fromisoformat(s.last_analyze_at) if s.last_analyze_at else None
                if not last or now >= last + timedelta(minutes=s.analyze_interval_minutes):
                    task_id = _create_task("analyze")
                    asyncio.create_task(_run_analyze(task_id, AnalyzeRequest()))
                    cfg = load_config()
                    cfg.scheduler.last_analyze_at = now.isoformat()
                    save_config(cfg)
        except Exception as e:
            logging.warning("scheduler loop error: %s", e)


# ---- Dashboard ----

@app.get("/api/dashboard")
async def dashboard():
    import aiosqlite

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row

        total = await db.execute_fetchall("SELECT COUNT(*) as c FROM messages")
        total_messages = total[0]["c"] if total else 0

        chats = await db.execute_fetchall("SELECT COUNT(DISTINCT chat_id) as c FROM messages")
        total_chats = chats[0]["c"] if chats else 0

        knowledge_count = await db.execute_fetchall("SELECT COUNT(*) as c FROM knowledge_items")
        total_knowledge = knowledge_count[0]["c"] if knowledge_count else 0

        platforms = await db.execute_fetchall(
            "SELECT platform, COUNT(*) as count FROM messages GROUP BY platform"
        )

        recent = await db.execute_fetchall(
            "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT 10"
        )

        # Last 30 days, per platform per day
        daily = await db.execute_fetchall(
            """SELECT substr(timestamp, 1, 10) as date, platform, COUNT(*) as count
               FROM messages
               WHERE timestamp >= datetime('now', '-30 days')
               GROUP BY date, platform
               ORDER BY date"""
        )
        recent_active_row = await db.execute_fetchall(
            "SELECT COUNT(*) as c FROM messages WHERE timestamp >= datetime('now', '-7 days')"
        )

    return {
        "total_messages": total_messages,
        "total_chats": total_chats,
        "total_knowledge": total_knowledge,
        "platforms": [dict(r) for r in platforms],
        "recent_knowledge": [dict(r) for r in recent],
        "daily_counts": [dict(r) for r in daily],
        "recent_active": recent_active_row[0]["c"] if recent_active_row else 0,
    }


# ---- Messages ----

@app.get("/api/messages")
async def get_messages(
    platform: Optional[str] = None,
    chat: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    category: Optional[str] = None,
    urgency_min: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
):
    import aiosqlite

    conditions = []
    params = []

    if platform:
        conditions.append("m.platform = ?")
        params.append(platform)
    if chat:
        conditions.append("m.chat_name LIKE ?")
        params.append(f"%{chat}%")
    if since:
        conditions.append("m.timestamp >= ?")
        params.append(since)
    if until:
        conditions.append("m.timestamp <= ?")
        params.append(until)
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    if urgency_min is not None:
        conditions.append("a.urgency >= ?")
        params.append(urgency_min)

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT m.*, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                {where}
                ORDER BY m.timestamp DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )

    return [dict(r) for r in rows]


# ---- Chats ----

@app.get("/api/chats")
async def get_chats():
    import aiosqlite

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT platform, chat_id, chat_name, chat_type,
                      COUNT(*) as msg_count,
                      MIN(timestamp) as earliest,
                      MAX(timestamp) as latest
               FROM messages
               GROUP BY platform, chat_id
               ORDER BY latest DESC"""
        )

    return [dict(r) for r in rows]


# ---- Search ----

@app.get("/api/search")
async def search(
    keyword: str,
    platform: Optional[str] = None,
    category: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    import aiosqlite

    conditions = ["m.content LIKE ?"]
    params: list = [f"%{keyword}%"]

    if platform:
        conditions.append("m.platform = ?")
        params.append(platform)
    if category:
        conditions.append("a.category = ?")
        params.append(category)
    if since:
        conditions.append("m.timestamp >= ?")
        params.append(since)
    if until:
        conditions.append("m.timestamp <= ?")
        params.append(until)

    where = " AND ".join(conditions)

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""SELECT m.*, a.category, a.urgency, a.summary
                FROM messages m
                LEFT JOIN analysis_results a ON a.message_id = m.id
                WHERE {where}
                ORDER BY m.timestamp DESC LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )

    return [dict(r) for r in rows]


# ---- Config ----

@app.get("/api/config")
async def get_config():
    data = load_config().model_dump()
    if data.get("llm", {}).get("api_key"):
        data["llm"]["api_key"] = "********"
    if data.get("qq", {}).get("token"):
        data["qq"]["token"] = "********"
    if data.get("telegram", {}).get("session_string"):
        data["telegram"]["session_string"] = "********"
    return data


class ConfigUpdate(BaseModel):
    filter_mode: Optional[str] = None
    add_chat: Optional[str] = None
    remove_chat: Optional[str] = None
    add_vip: Optional[str] = None
    remove_vip: Optional[str] = None
    budget: Optional[int] = None
    daily_token_budget: Optional[int] = None
    budget_action: Optional[str] = None
    vip_contacts: Optional[list[str]] = None
    llm_provider: Optional[str] = None
    llm_api_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    qq_enabled: Optional[bool] = None
    qq_host: Optional[str] = None
    qq_port: Optional[int] = None
    qq_token: Optional[str] = None  # "********" means keep current
    telegram_enabled: Optional[bool] = None  # session_string is set by login flow only


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    cfg = load_config()

    if body.filter_mode:
        cfg.chat_filter.mode = body.filter_mode
    if body.add_chat and body.add_chat not in cfg.chat_filter.chats:
        cfg.chat_filter.chats.append(body.add_chat)
    if body.remove_chat and body.remove_chat in cfg.chat_filter.chats:
        cfg.chat_filter.chats.remove(body.remove_chat)
    if body.add_vip and body.add_vip not in cfg.vip_contacts:
        cfg.vip_contacts.append(body.add_vip)
    if body.remove_vip and body.remove_vip in cfg.vip_contacts:
        cfg.vip_contacts.remove(body.remove_vip)
    if body.budget is not None:
        cfg.daily_token_budget = body.budget
    if body.daily_token_budget is not None:
        cfg.daily_token_budget = body.daily_token_budget
    if body.budget_action is not None:
        cfg.budget_action = body.budget_action
    if body.vip_contacts is not None:
        cfg.vip_contacts = body.vip_contacts
    if body.llm_provider:
        cfg.llm.provider = body.llm_provider
    if body.llm_api_url is not None:
        cfg.llm.api_url = body.llm_api_url
    if body.llm_model is not None:
        cfg.llm.model = body.llm_model
    if body.llm_api_key is not None and body.llm_api_key != "********":
        cfg.llm.api_key = body.llm_api_key
    if body.qq_enabled is not None:
        cfg.qq.enabled = body.qq_enabled
    if body.qq_host is not None:
        cfg.qq.host = body.qq_host
    if body.qq_port is not None:
        cfg.qq.port = body.qq_port
    if body.qq_token is not None and body.qq_token != "********":
        cfg.qq.token = body.qq_token
    if body.telegram_enabled is not None:
        cfg.telegram.enabled = body.telegram_enabled

    save_config(cfg)
    data = cfg.model_dump()
    if data.get("llm", {}).get("api_key"):
        data["llm"]["api_key"] = "********"
    if data.get("qq", {}).get("token"):
        data["qq"]["token"] = "********"
    if data.get("telegram", {}).get("session_string"):
        data["telegram"]["session_string"] = "********"
    return data


# ---- Sync / Import / Analyze (async tasks with SSE) ----

class SyncRequest(BaseModel):
    new_only: bool = False


class ImportRequest(BaseModel):
    path: str


class AnalyzeRequest(BaseModel):
    chat: Optional[str] = None
    chats: Optional[list[str]] = None
    since: Optional[str] = None
    until: Optional[str] = None
    limit: int = 100
    full: bool = False  # if True, ignore incremental and analyze all


# ---- LLM Models ----

@app.get("/api/llm/models")
async def get_llm_models():
    cfg = load_config()
    if cfg.llm.provider != "openai_compatible":
        return {"provider": "claude_cli", "models": []}
    url = cfg.llm.api_url.replace("localhost", "127.0.0.1")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{url}/models",
                headers={"Authorization": f"Bearer {cfg.llm.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            models = [m["id"] for m in data.get("data", [])]
            return {"provider": "openai_compatible", "models": models}
    except Exception as e:
        raise HTTPException(502, f"无法连接到 LLM 服务: {e}")


@app.get("/api/llm/test")
async def test_llm_connection():
    cfg = load_config()
    if cfg.llm.provider != "openai_compatible":
        return {"status": "ok", "provider": "claude_cli"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{cfg.llm.api_url.replace('localhost', '127.0.0.1')}/models",
                headers={"Authorization": f"Bearer {cfg.llm.api_key}"},
            )
            resp.raise_for_status()
            return {"status": "ok", "provider": "openai_compatible"}
    except Exception as e:
        raise HTTPException(502, f"连接失败: {e}")


def _create_task(task_type: str) -> str:
    task_id = uuid.uuid4().hex[:8]
    _tasks[task_id] = {"type": task_type, "status": "pending", "progress": 0, "message": ""}
    return task_id


async def _run_sync(task_id: str, new_only: bool):
    from app.services.sync.wechat import sync_sessions, sync_new_messages

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步微信数据..."
    _tasks[task_id]["progress"] = 30

    try:
        count = await (sync_new_messages() if new_only else sync_sessions())
        _tasks[task_id].update(status="done", progress=100, message=f"同步完成，新增 {count} 条消息")
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def _run_import(task_id: str, platform: str, path: str):
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = f"正在导入 {platform} 数据..."
    _tasks[task_id]["progress"] = 30

    try:
        if platform == "qq":
            from app.services.sync.qq import import_qq_json, import_qq_dir
            result = await (import_qq_dir(path) if Path(path).is_dir() else import_qq_json(path))
        else:
            from app.services.sync.telegram import import_telegram_json, import_telegram_dir
            result = await (import_telegram_dir(path) if Path(path).is_dir() else import_telegram_json(path))

        _tasks[task_id].update(
            status="done", progress=100,
            message=f"导入完成: {result['imported']} 条消息 ({result.get('chats', 1)} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


def _make_progress(task_id: str):
    def cb(pct: int, msg: str):
        _tasks[task_id].update(progress=pct, message=msg)
    return cb


async def _run_qq_sync(task_id: str):
    from app.services.sync.qq_qce import sync_all
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 QQ..."
    _tasks[task_id]["progress"] = 5
    try:
        result = await sync_all(progress=_make_progress(task_id))
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个聊天)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def _run_telegram_sync(task_id: str):
    from app.services.sync.telegram_live import sync_all
    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在同步 Telegram..."
    _tasks[task_id]["progress"] = 5
    try:
        result = await sync_all(progress=_make_progress(task_id))
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"同步完成: {result['imported']} 条消息 ({result['chats']} 个对话)",
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


async def _run_analyze(task_id: str, req: AnalyzeRequest):
    import aiosqlite
    from app.services.analyzer import AnalyzerService

    _tasks[task_id]["status"] = "running"
    _tasks[task_id]["message"] = "正在分析消息..."
    _tasks[task_id]["progress"] = 10

    try:
        svc = AnalyzerService()
        cfg = load_config()

        # Determine chat names to analyze
        chat_names = req.chats or ([req.chat] if req.chat else None)

        async with aiosqlite.connect(str(DB_PATH)) as db:
            db.row_factory = aiosqlite.Row

            conditions = []
            params: list = []

            if chat_names:
                placeholders = ",".join("?" * len(chat_names))
                conditions.append(f"chat_name IN ({placeholders})")
                params.extend(chat_names)
            else:
                if cfg.chat_filter.chats:
                    placeholders = ",".join("?" * len(cfg.chat_filter.chats))
                    if cfg.chat_filter.mode == "whitelist":
                        conditions.append(f"chat_name IN ({placeholders})")
                    else:
                        conditions.append(f"chat_name NOT IN ({placeholders})")
                    params.extend(cfg.chat_filter.chats)

            if req.since:
                conditions.append("timestamp >= ?")
                params.append(req.since)
            elif not req.full:
                # incremental: only analyze messages after last analyzed timestamp
                last = await db.execute_fetchall(
                    "SELECT MAX(created_at) as last FROM knowledge_items"
                )
                last_ts = last[0]["last"] if last and last[0]["last"] else None
                if last_ts:
                    # find the latest source message timestamp from last batch
                    last_msg = await db.execute_fetchall(
                        """SELECT MAX(m.timestamp) as last_ts FROM messages m
                           JOIN knowledge_items k ON (
                               k.source_message_ids = ('[' || m.id || ']')
                               OR k.source_message_ids LIKE ('[' || m.id || ',%')
                               OR k.source_message_ids LIKE ('%,' || m.id || ']')
                               OR k.source_message_ids LIKE ('%,' || m.id || ',%')
                           )
                           WHERE k.created_at >= ?""",
                        (last_ts,),
                    )
                    since_ts = last_msg[0]["last_ts"] if last_msg and last_msg[0]["last_ts"] else None
                    if since_ts:
                        conditions.append("timestamp > ?")
                        params.append(since_ts)

            if req.until:
                conditions.append("timestamp <= ?")
                params.append(req.until)

            where = " AND ".join(conditions)
            sql = f"""SELECT id, chat_id, chat_name, sender_name, content, msg_type, timestamp
                      FROM messages {'WHERE ' + where if where else ''}
                      ORDER BY timestamp DESC LIMIT ?"""
            params.append(req.limit)
            rows = await db.execute_fetchall(sql, params)

        _tasks[task_id]["progress"] = 30
        _tasks[task_id]["message"] = f"找到 {len(rows)} 条消息，开始分析..."

        messages = [dict(r) for r in rows]

        def on_progress(pct, msg):
            _tasks[task_id].update(progress=pct, message=msg)

        knowledge_items, summary = await svc.analyze_messages(messages, on_progress=on_progress)
        _pending_results[task_id] = knowledge_items

        _tasks[task_id].update(
            status="done", progress=100,
            message=f"提取到 {len(knowledge_items)} 个知识点，请筛选后保存",
            result_count=len(knowledge_items),
            summary=summary,
        )
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))


@app.post("/api/sync/wechat")
async def sync_wechat(body: SyncRequest = SyncRequest()):
    task_id = _create_task("sync_wechat")
    asyncio.create_task(_run_sync(task_id, body.new_only))
    return {"task_id": task_id}


@app.post("/api/import/qq")
async def import_qq(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = _create_task("import_qq")
    asyncio.create_task(_run_import(task_id, "qq", body.path))
    return {"task_id": task_id}


@app.post("/api/import/telegram")
async def import_telegram(body: ImportRequest):
    if not Path(body.path).exists():
        raise HTTPException(400, f"File not found: {body.path}")
    task_id = _create_task("import_telegram")
    asyncio.create_task(_run_import(task_id, "telegram", body.path))
    return {"task_id": task_id}


# ---- QQ (NapCat-QCE) ----

@app.post("/api/qq/test")
async def qq_test():
    from app.services.sync.qq_qce import test_connection, QCEError
    cfg = load_config().qq
    if not cfg.token:
        raise HTTPException(400, "请先填写 QCE token")
    try:
        return await test_connection(cfg)
    except QCEError as e:
        raise HTTPException(400, str(e))
    except httpx.HTTPError as e:
        raise HTTPException(502, f"无法连接 QCE: {e}")


@app.post("/api/sync/qq")
async def sync_qq():
    if _is_task_running("sync_qq"):
        raise HTTPException(409, "QQ 同步正在运行")
    task_id = _create_task("sync_qq")
    asyncio.create_task(_run_qq_sync(task_id))
    return {"task_id": task_id}


# ---- QQ Launcher (Docker-managed NapCat + QCE) ----

async def _run_qq_install(task_id: str, force: bool):
    from app.services.sync.qq_launcher import ensure_installed, LauncherError
    _tasks[task_id].update(status="running", progress=5, message="拉取 QCE release 信息...")
    try:
        result = await ensure_installed(force=force)
        _tasks[task_id].update(
            status="done", progress=100,
            message=f"已安装 {result['version']}" + (" (已是最新)" if result["already_installed"] else ""),
            **result,
        )
    except LauncherError as e:
        _tasks[task_id].update(status="error", progress=0, message=str(e))
    except Exception as e:
        _tasks[task_id].update(status="error", progress=0, message=f"安装失败: {e}")


@app.get("/api/qq/launcher/status")
async def qq_launcher_status():
    from app.services.sync.qq_launcher import status
    return await status()


@app.post("/api/qq/launcher/install")
async def qq_launcher_install(force: bool = False):
    if _is_task_running("qq_install"):
        raise HTTPException(409, "安装正在进行中")
    task_id = _create_task("qq_install")
    asyncio.create_task(_run_qq_install(task_id, force))
    return {"task_id": task_id}


@app.post("/api/qq/launcher/start")
async def qq_launcher_start():
    from app.services.sync.qq_launcher import start, LauncherError
    try:
        return await start()
    except LauncherError as e:
        raise HTTPException(400, str(e))


@app.post("/api/qq/launcher/stop")
async def qq_launcher_stop():
    from app.services.sync.qq_launcher import stop, LauncherError
    try:
        return await stop()
    except LauncherError as e:
        raise HTTPException(400, str(e))


@app.get("/api/qq/launcher/logs")
async def qq_launcher_logs(tail: int = 200):
    from app.services.sync.qq_launcher import logs
    return {"logs": await logs(tail=tail)}


# ---- Telegram (Telethon) ----

class TelegramLoginStart(BaseModel):
    api_id: int
    api_hash: str
    phone: str


class TelegramLoginConfirm(BaseModel):
    phone: str
    code: str
    password: Optional[str] = None


@app.post("/api/telegram/login/start")
async def telegram_login_start(body: TelegramLoginStart):
    from app.services.sync.telegram_live import start_login
    try:
        return await start_login(body.api_id, body.api_hash, body.phone)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/api/telegram/login/confirm")
async def telegram_login_confirm(body: TelegramLoginConfirm):
    from app.services.sync.telegram_live import confirm_code
    try:
        return await confirm_code(body.phone, body.code, body.password)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/api/telegram/status")
async def telegram_status():
    from app.services.sync.telegram_live import status
    try:
        return await status()
    except Exception as e:
        return {"logged_in": False, "error": str(e)}


@app.post("/api/telegram/logout")
async def telegram_logout():
    from app.services.sync.telegram_live import logout
    await logout()
    return {"ok": True}


@app.post("/api/sync/telegram")
async def sync_telegram():
    if _is_task_running("sync_telegram"):
        raise HTTPException(409, "Telegram 同步正在运行")
    task_id = _create_task("sync_telegram")
    asyncio.create_task(_run_telegram_sync(task_id))
    return {"task_id": task_id}


# ---- Tasks ----

@app.get("/api/tasks")
async def list_tasks():
    """All in-memory tasks. Used by the global task bar in the UI."""
    return [{"id": tid, **t} for tid, t in _tasks.items()]


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest = AnalyzeRequest()):
    task_id = _create_task("analyze")
    asyncio.create_task(_run_analyze(task_id, body))
    return {"task_id": task_id}


@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def event_stream():
        while True:
            if task_id in _tasks:
                data = json.dumps(_tasks[task_id])
                yield f"data: {data}\n\n"
                if _tasks[task_id]["status"] in ("done", "error"):
                    break
            await asyncio.sleep(0.5)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---- Analysis Review ----

@app.get("/api/analyze/{task_id}/results")
async def get_analysis_results(task_id: str):
    if task_id not in _pending_results:
        raise HTTPException(404, "分析结果不存在或已过期")
    return _pending_results[task_id]


class ConfirmRequest(BaseModel):
    ids: list[int]  # indices into pending results to save


@app.post("/api/analyze/{task_id}/confirm")
async def confirm_analysis(task_id: str, body: ConfirmRequest):
    import aiosqlite
    from app.services.knowledge import save_knowledge_items

    if task_id not in _pending_results:
        raise HTTPException(404, "分析结果不存在或已过期")

    results = _pending_results.pop(task_id)
    save_indices = set(body.ids)
    to_save = [r for i, r in enumerate(results) if i in save_indices]

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await save_knowledge_items(db, to_save)
        await db.commit()

    return {"saved": len(to_save), "skipped": len(results) - len(to_save)}


class ExtendRequest(BaseModel):
    id: int


@app.post("/api/knowledge/{item_id}/extend")
async def extend_knowledge(item_id: int):
    import aiosqlite
    from app.services.analyzer import AnalyzerService

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM knowledge_items WHERE id=?", (item_id,))
        if not rows:
            raise HTTPException(404, "知识点不存在")
        item = dict(rows[0])

    svc = AnalyzerService()
    extended = await svc.extend_knowledge(item)

    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("UPDATE knowledge_items SET extended_content=? WHERE id=?", (extended, item_id))
        await db.commit()

    return {"extended_content": extended}


@app.get("/api/knowledge")
async def list_knowledge(q: Optional[str] = None, limit: int = 50, offset: int = 0):
    import aiosqlite

    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        if q:
            rows = await db.execute_fetchall(
                """SELECT * FROM knowledge_items WHERE title LIKE ? OR content LIKE ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (f"%{q}%", f"%{q}%", limit, offset),
            )
        else:
            rows = await db.execute_fetchall(
                "SELECT * FROM knowledge_items ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
    return [dict(r) for r in rows]


@app.delete("/api/knowledge/{item_id}")
async def delete_knowledge(item_id: int):
    import aiosqlite
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        await db.commit()
    return {"ok": True}


class KnowledgeUpdateRequest(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    tags: Optional[list[str]] = None


@app.patch("/api/knowledge/{item_id}")
async def update_knowledge(item_id: int, body: KnowledgeUpdateRequest):
    import aiosqlite
    updates = {}
    if body.title is not None: updates["title"] = body.title
    if body.content is not None: updates["content"] = body.content
    if body.tags is not None: updates["tags"] = json.dumps(body.tags, ensure_ascii=False)
    if not updates:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k}=?" for k in updates)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(f"UPDATE knowledge_items SET {set_clause} WHERE id=?", [*updates.values(), item_id])
        await db.commit()
    return {"ok": True}


@app.get("/api/knowledge/export")
async def export_knowledge(fmt: str = "markdown"):
    import aiosqlite
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM knowledge_items ORDER BY created_at DESC")
    items = [dict(r) for r in rows]

    if fmt == "json":
        return items

    # markdown export
    lines = ["# 知识库\n"]
    for item in items:
        tags = json.loads(item.get("tags") or "[]")
        lines.append(f"## {item['title']}")
        if tags: lines.append(f"**标签**: {', '.join(tags)}")
        if item.get("source_chat"): lines.append(f"**来源**: {item['source_chat']}")
        lines.append(f"\n{item['content']}")
        if item.get("extended_content"):
            lines.append(f"\n### 扩展知识\n{item['extended_content']}")
        lines.append("\n---\n")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


# ---- Scheduler ----

def _next_run(last_at: str | None, interval_min: int) -> str | None:
    if not last_at or interval_min <= 0:
        return None
    last = datetime.fromisoformat(last_at)
    return (last + timedelta(minutes=interval_min)).isoformat()


@app.get("/api/scheduler")
async def get_scheduler():
    cfg = load_config()
    s = cfg.scheduler
    return {
        "sync_enabled": s.sync_enabled,
        "sync_interval_minutes": s.sync_interval_minutes,
        "last_sync_at": s.last_sync_at,
        "next_sync_at": _next_run(s.last_sync_at, s.sync_interval_minutes) if s.sync_enabled else None,
        "qq_enabled": s.qq_enabled,
        "qq_interval_minutes": s.qq_interval_minutes,
        "last_qq_sync_at": s.last_qq_sync_at,
        "next_qq_sync_at": _next_run(s.last_qq_sync_at, s.qq_interval_minutes) if s.qq_enabled else None,
        "telegram_enabled": s.telegram_enabled,
        "telegram_interval_minutes": s.telegram_interval_minutes,
        "last_telegram_sync_at": s.last_telegram_sync_at,
        "next_telegram_sync_at": _next_run(s.last_telegram_sync_at, s.telegram_interval_minutes) if s.telegram_enabled else None,
        "analyze_enabled": s.analyze_enabled,
        "analyze_interval_minutes": s.analyze_interval_minutes,
        "last_analyze_at": s.last_analyze_at,
        "next_analyze_at": _next_run(s.last_analyze_at, s.analyze_interval_minutes) if s.analyze_enabled else None,
    }


class SchedulerUpdate(BaseModel):
    sync_enabled: Optional[bool] = None
    sync_interval_minutes: Optional[int] = None
    analyze_enabled: Optional[bool] = None
    analyze_interval_minutes: Optional[int] = None
    qq_enabled: Optional[bool] = None
    qq_interval_minutes: Optional[int] = None
    telegram_enabled: Optional[bool] = None
    telegram_interval_minutes: Optional[int] = None


@app.put("/api/scheduler")
async def update_scheduler(body: SchedulerUpdate):
    cfg = load_config()
    if body.sync_enabled is not None:
        cfg.scheduler.sync_enabled = body.sync_enabled
    if body.sync_interval_minutes is not None:
        cfg.scheduler.sync_interval_minutes = body.sync_interval_minutes
    if body.analyze_enabled is not None:
        cfg.scheduler.analyze_enabled = body.analyze_enabled
    if body.analyze_interval_minutes is not None:
        cfg.scheduler.analyze_interval_minutes = body.analyze_interval_minutes
    if body.qq_enabled is not None:
        cfg.scheduler.qq_enabled = body.qq_enabled
    if body.qq_interval_minutes is not None:
        cfg.scheduler.qq_interval_minutes = body.qq_interval_minutes
    if body.telegram_enabled is not None:
        cfg.scheduler.telegram_enabled = body.telegram_enabled
    if body.telegram_interval_minutes is not None:
        cfg.scheduler.telegram_interval_minutes = body.telegram_interval_minutes
    save_config(cfg)
    return await get_scheduler()


# ---- Serve frontend static files (must be last) ----

if FRONTEND_DIST.exists():
    from starlette.responses import Response

    def _safe_path(base: Path, user_path: str) -> Path | None:
        resolved = (base / user_path).resolve()
        if not str(resolved).startswith(str(base.resolve())):
            return None
        return resolved

    @app.get("/assets/{file_path:path}")
    async def serve_assets(file_path: str):
        safe = _safe_path(FRONTEND_DIST / "assets", file_path)
        if not safe or not safe.is_file():
            raise HTTPException(404)
        return FileResponse(safe)

    @app.get("/")
    async def serve_index():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        safe = _safe_path(FRONTEND_DIST, full_path)
        if safe and safe.is_file():
            return FileResponse(safe)
        return FileResponse(FRONTEND_DIST / "index.html")
