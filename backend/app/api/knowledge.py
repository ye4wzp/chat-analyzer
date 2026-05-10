import json
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.core import database
from app.models.knowledge import KnowledgeUpdateRequest

router = APIRouter()


@router.post("/api/knowledge/{item_id}/extend")
async def extend_knowledge(item_id: int):
    from app.services.analyzer import AnalyzerService

    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM knowledge_items WHERE id=?", (item_id,))
        if not rows:
            raise HTTPException(404, "知识点不存在")
        item = dict(rows[0])

    svc = AnalyzerService()
    extended = await svc.extend_knowledge(item)

    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        await db.execute("UPDATE knowledge_items SET extended_content=? WHERE id=?", (extended, item_id))
        await db.commit()

    return {"extended_content": extended}


@router.get("/api/knowledge")
async def list_knowledge(q: Optional[str] = None, limit: int = 50, offset: int = 0):
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
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


@router.delete("/api/knowledge/{item_id}")
async def delete_knowledge(item_id: int):
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        await db.execute("DELETE FROM knowledge_items WHERE id=?", (item_id,))
        await db.commit()
    return {"ok": True}


@router.patch("/api/knowledge/{item_id}")
async def update_knowledge(item_id: int, body: KnowledgeUpdateRequest):
    updates: dict = {}
    if body.title is not None:
        updates["title"] = body.title
    if body.content is not None:
        updates["content"] = body.content
    if body.tags is not None:
        updates["tags"] = json.dumps(body.tags, ensure_ascii=False)
    if not updates:
        raise HTTPException(400, "No fields to update")
    set_clause = ", ".join(f"{k}=?" for k in updates)
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        await db.execute(f"UPDATE knowledge_items SET {set_clause} WHERE id=?", [*updates.values(), item_id])
        await db.commit()
    return {"ok": True}


@router.get("/api/knowledge/export")
async def export_knowledge(fmt: str = "markdown"):
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM knowledge_items ORDER BY created_at DESC")
    items = [dict(r) for r in rows]

    if fmt == "json":
        return items

    lines = ["# 知识库\n"]
    for item in items:
        tags = json.loads(item.get("tags") or "[]")
        lines.append(f"## {item['title']}")
        if tags:
            lines.append(f"**标签**: {', '.join(tags)}")
        if item.get("source_chat"):
            lines.append(f"**来源**: {item['source_chat']}")
        lines.append(f"\n{item['content']}")
        if item.get("extended_content"):
            lines.append(f"\n### 扩展知识\n{item['extended_content']}")
        lines.append("\n---\n")

    return PlainTextResponse("\n".join(lines), media_type="text/markdown")
