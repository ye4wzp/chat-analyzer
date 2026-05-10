from fastapi import APIRouter, HTTPException

from app.services.backup import (
    create_backup,
    delete_backup,
    list_backups,
)

router = APIRouter()


@router.get("/api/backups")
async def get_backups():
    """List all backup files (newest first)."""
    return list_backups()


@router.post("/api/backups")
async def post_backup():
    """Trigger a fresh backup now. Returns the new file's metadata."""
    try:
        return await create_backup()
    except Exception as e:
        raise HTTPException(500, f"备份失败: {e}")


@router.delete("/api/backups/{name}")
async def remove_backup(name: str):
    if not delete_backup(name):
        raise HTTPException(404, "备份不存在或路径非法")
    return {"ok": True}
