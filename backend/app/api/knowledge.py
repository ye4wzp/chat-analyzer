import json
from typing import Optional

import aiosqlite
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from app.core import database
from app.core.runners import run_embed_knowledge
from app.core.tasks import create_task, is_task_running, spawn_cancellable
from app.models.knowledge import KnowledgeUpdateRequest

router = APIRouter()


@router.post("/api/knowledge/embed")
async def trigger_embed():
    """Kick off background embedding job for all unindexed knowledge_items."""
    if is_task_running("embed"):
        raise HTTPException(409, "Embedding 任务已在运行")
    task_id = create_task("embed")
    spawn_cancellable(task_id, run_embed_knowledge(task_id))
    return {"task_id": task_id}


@router.get("/api/knowledge/embed/status")
async def embed_status():
    """How many knowledge_items are indexed under the current embedding model."""
    from app.core.config import load_config
    cfg = load_config()
    model = cfg.llm.embedding_model
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        total_row = await db.execute_fetchall("SELECT COUNT(*) AS n FROM knowledge_items")
        total = int(total_row[0]["n"]) if total_row else 0
        if not model:
            return {"model": "", "total": total, "indexed": 0, "stale": 0}
        indexed_row = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM knowledge_items WHERE embedding IS NOT NULL AND embedding_model = ?",
            (model,),
        )
        indexed = int(indexed_row[0]["n"]) if indexed_row else 0
        stale_row = await db.execute_fetchall(
            "SELECT COUNT(*) AS n FROM knowledge_items WHERE embedding IS NOT NULL AND embedding_model <> ?",
            (model,),
        )
        stale = int(stale_row[0]["n"]) if stale_row else 0
    return {"model": model, "total": total, "indexed": indexed, "stale": stale}


@router.post("/api/knowledge/{item_id}/extend")
async def extend_knowledge(item_id: int):
    from app.services.analyzer import AnalyzerService

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT * FROM knowledge_items WHERE id=?", (item_id,))
        if not rows:
            raise HTTPException(404, "知识点不存在")
        item = dict(rows[0])

    svc = AnalyzerService()
    extended = await svc.extend_knowledge(item)

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        await db.execute("UPDATE knowledge_items SET extended_content=? WHERE id=?", (extended, item_id))
        await db.commit()

    return {"extended_content": extended}


@router.get("/api/knowledge/{item_id}/related")
async def related_items(item_id: int, limit: int = 3):
    """Top-K similar knowledge_items by embedding cosine. Returns [] if the
    target itself isn't embedded yet (UI shows nothing rather than misleading
    keyword fallback)."""
    from app.services.embedder import deserialize_vec, cosine

    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        target_rows = await db.execute_fetchall(
            "SELECT * FROM knowledge_items WHERE id=?", (item_id,)
        )
        if not target_rows:
            raise HTTPException(404, "知识点不存在")
        target = dict(target_rows[0])
        if not target.get("embedding"):
            return []

        target_vec = deserialize_vec(target["embedding"])
        rows = await db.execute_fetchall(
            "SELECT * FROM knowledge_items WHERE id <> ? AND embedding IS NOT NULL",
            (item_id,),
        )

    scored = []
    for r in rows:
        d = dict(r)
        vec = deserialize_vec(d["embedding"])
        scored.append((cosine(target_vec, vec), d))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, item in scored[:limit]:
        item.pop("embedding", None)
        item["similarity"] = round(score, 4)
        out.append(item)
    return out


@router.get("/api/knowledge")
async def list_knowledge(
    q: Optional[str] = None,
    mode: str = "keyword",
    limit: int = 50,
    offset: int = 0,
):
    """List knowledge items.

    `mode=keyword` (default): SQL LIKE on title/content.
    `mode=semantic`: embed `q` once, cosine-rank all indexed items, return top
      `limit` (offset honored). Returns 0 results if no items are embedded yet —
      callers should surface "请先到 Knowledge 页点'索引'" in that case.
    """
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row

        if q and mode == "semantic":
            from app.services.embedder import embed_text, deserialize_vec, cosine
            query_vec = await embed_text(q)
            if not query_vec:
                return []
            rows = await db.execute_fetchall(
                "SELECT * FROM knowledge_items WHERE embedding IS NOT NULL"
            )
            scored = []
            for r in rows:
                vec = deserialize_vec(r["embedding"])
                scored.append((cosine(query_vec, vec), dict(r)))
            scored.sort(key=lambda x: x[0], reverse=True)
            results = []
            for score, item in scored[offset : offset + limit]:
                # Drop the BLOB so we don't ship 3KB×N over the wire; surface score for UI sort indicator.
                item.pop("embedding", None)
                item["similarity"] = round(score, 4)
                results.append(item)
            return results

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
    out = []
    for r in rows:
        d = dict(r)
        d.pop("embedding", None)  # never send the BLOB to the client
        out.append(d)
    return out


@router.delete("/api/knowledge/{item_id}")
async def delete_knowledge(item_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
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
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        await db.execute(f"UPDATE knowledge_items SET {set_clause} WHERE id=?", [*updates.values(), item_id])
        await db.commit()
    return {"ok": True}


@router.get("/api/knowledge/export")
async def export_knowledge(fmt: str = "markdown"):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
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
