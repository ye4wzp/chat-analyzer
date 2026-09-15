import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    aliases,
    analyze,
    ask,
    backups,
    chats,
    config,
    contacts,
    dashboard,
    inbox,
    knowledge,
    mentions,
    messages,
    payments,
    qq,
    reports,
    scheduler,
    search,
    static,
    stats,
    sync,
    tags,
    tasks,
    telegram,
    todos,
)
from app.core.config import get_api_token
from app.core.database import DB_PATH, init_db
from app.core.scheduler import scheduler_loop
from app.core.tasks import _tasks
from app.models.analyze import ConfirmRequest
from app.models.config import ConfigUpdate

FRONTEND_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # In-memory task/lock state means exactly one server process per data dir.
    from app.core.config import DATA_DIR
    from app.core.instance_lock import acquire as acquire_instance_lock, release as release_instance_lock
    acquire_instance_lock(DATA_DIR)

    await init_db()
    loop_task = asyncio.create_task(scheduler_loop())
    # Bring the stats rollups up to date in the background (no-op when current).
    from app.services.stats import refresh_on_startup
    asyncio.create_task(refresh_on_startup())
    try:
        yield
    finally:
        loop_task.cancel()
        release_instance_lock()


ALLOWED_ORIGINS = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:8000", "http://127.0.0.1:8000",
]
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

app = FastAPI(title="Chat Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _host_without_port(raw: str) -> str:
    host = raw.strip().lower()
    if host.startswith("["):          # [::1]:8000 / [::1]
        return host.split("]", 1)[0] + "]"
    if host.count(":") == 1:           # host:port
        return host.split(":", 1)[0]
    return host                        # bare hostname or bracket-less IPv6


@app.middleware("http")
async def api_security_guard(request: Request, call_next):
    """Localhost-app hardening for /api:
    - DNS-rebinding: on the default token-less server, reject requests whose Host
      isn't loopback (a rebound attacker page sends its own hostname). When a
      token is set (LAN mode) the token is the guard, so any Host is allowed.
    - CSRF: a cross-site page can fire simple/no-cors state-changing requests it
      can't read the response of; reject non-GET carrying a foreign Origin.
    - Optional shared-secret auth for LAN exposure (EventSource passes ?token=).
    """
    if request.url.path.startswith("/api"):
        token = get_api_token()

        if not token:
            host = _host_without_port(request.headers.get("host") or "")
            if host and host not in LOCAL_HOSTS:
                return JSONResponse({"detail": "invalid host"}, status_code=403)

        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in ALLOWED_ORIGINS:
                return JSONResponse({"detail": "cross-origin request blocked"}, status_code=403)

        if token:
            supplied = (
                request.headers.get("x-api-token")
                or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
                or request.query_params.get("token", "")
            )
            if supplied != token:
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


for module in (
    dashboard, messages, chats, search, config, ask, reports, stats, contacts, mentions, payments,
    sync, qq, telegram, tasks, analyze, knowledge, scheduler, backups, inbox, tags, todos, aliases,
):
    app.include_router(module.router)

# SPA static handlers must be registered last so they don't shadow API routes.
static.register(app, FRONTEND_DIST)

# Re-exports kept for tests and external callers that imported from app.main.
from app.api.analyze import confirm_analysis  # noqa: E402,F401
from app.api.config import update_config  # noqa: E402,F401

__all__ = [
    "app",
    "ConfigUpdate",
    "ConfirmRequest",
    "confirm_analysis",
    "update_config",
    "_tasks",
    "DB_PATH",
]
