from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()


def _safe_path(base: Path, user_path: str) -> Path | None:
    resolved = (base / user_path).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        return None
    return resolved


def register(app, frontend_dist: Path) -> None:
    """Mount SPA static handlers. Must be called *after* all API routers are included."""
    if not frontend_dist.exists():
        return

    @app.get("/assets/{file_path:path}")
    async def serve_assets(file_path: str):
        safe = _safe_path(frontend_dist / "assets", file_path)
        if not safe or not safe.is_file():
            raise HTTPException(404)
        return FileResponse(safe)

    @app.get("/")
    async def serve_index():
        return FileResponse(frontend_dist / "index.html")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        safe = _safe_path(frontend_dist, full_path)
        if safe and safe.is_file():
            return FileResponse(safe)
        return FileResponse(frontend_dist / "index.html")
