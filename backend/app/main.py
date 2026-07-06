import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import (
    aliases,
    analyze,
    backups,
    chats,
    config,
    dashboard,
    inbox,
    knowledge,
    messages,
    qq,
    scheduler,
    search,
    static,
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
    await init_db()
    loop_task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        loop_task.cancel()


app = FastAPI(title="Chat Analyzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_token_guard(request: Request, call_next):
    """Optional shared-secret auth for LAN exposure. Off unless config.api_token
    is set. EventSource can't send headers, so SSE clients pass ?token=."""
    token = get_api_token()
    if token and request.url.path.startswith("/api"):
        supplied = (
            request.headers.get("x-api-token")
            or request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            or request.query_params.get("token", "")
        )
        if supplied != token:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


for module in (
    dashboard, messages, chats, search, config,
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
