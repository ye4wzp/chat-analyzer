import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    analyze,
    backups,
    chats,
    config,
    dashboard,
    knowledge,
    messages,
    qq,
    scheduler,
    search,
    static,
    sync,
    tasks,
    telegram,
)
from app.core.database import DB_PATH, init_db
from app.core.scheduler import scheduler_loop
from app.core.tasks import _pending_results, _tasks
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

for module in (
    dashboard, messages, chats, search, config,
    sync, qq, telegram, tasks, analyze, knowledge, scheduler, backups,
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
    "_pending_results",
    "_tasks",
    "DB_PATH",
]
