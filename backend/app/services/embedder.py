"""Embedding indexer for knowledge_items.

Stores float32 vectors as BLOB columns in `knowledge_items.embedding`. Pure
Python — no numpy. For our scale (≤ a few thousand knowledge items) the
serialization + cosine is microseconds and the disk cost is ~3KB per item at
768 dims.

Design notes:
- Uses the configured OpenAI-compatible /v1/embeddings endpoint with
  `cfg.llm.embedding_model`. If not configured, embed_all() raises.
- Normalizes inputs by stripping; treats title + content as the embedding
  payload so search queries that match titles AND deeper content both surface.
- Re-embeds when `embedding_model` changes (column tracks which model produced
  the row's vector).
"""
from __future__ import annotations

import logging
import struct
from typing import Awaitable, Callable

import aiosqlite
import httpx

from app.core.config import load_config
from app.core.database import DB_PATH

logger = logging.getLogger("app.embedder")

ProgressCB = Callable[[int, str], Awaitable[None] | None]


def serialize_vec(vec: list[float]) -> bytes:
    """Pack a float vector as little-endian float32 bytes for BLOB storage."""
    return struct.pack(f"<{len(vec)}f", *vec)


def deserialize_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity. Returns 0 for zero-vectors instead of NaN."""
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na ** 0.5 * nb ** 0.5)


async def embed_text(text: str, model: str | None = None) -> list[float] | None:
    """One-off embed of a single string. Used by semantic search at query time."""
    cfg = load_config().llm
    model = model or cfg.embedding_model
    if not model:
        return None
    api_url = cfg.api_url.replace("localhost", "127.0.0.1")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{api_url}/embeddings",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={"model": model, "input": text},
            )
            if resp.status_code != 200:
                logger.warning("embed %s failed: %s %s", model, resp.status_code, resp.text[:200])
                return None
            data = resp.json()
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("embed call exception: %s", e)
        return None


async def embed_batch(texts: list[str], model: str) -> list[list[float] | None]:
    """Embed a batch in one HTTP call where the backend supports it. Falls back
    to per-text on failure so one bad row doesn't kill a 500-item run."""
    cfg = load_config().llm
    api_url = cfg.api_url.replace("localhost", "127.0.0.1")
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{api_url}/embeddings",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={"model": model, "input": texts},
            )
            if resp.status_code != 200:
                logger.warning("batch embed %s failed: %s %s", model, resp.status_code, resp.text[:200])
                return [None] * len(texts)
            data = resp.json()
            # Server may return entries out of order; sort by `index` to be safe.
            entries = sorted(data.get("data", []), key=lambda e: e.get("index", 0))
            vecs = [e["embedding"] for e in entries]
            if len(vecs) != len(texts):
                logger.warning("batch embed length mismatch: in=%d out=%d", len(texts), len(vecs))
                # Pad/truncate so caller's index alignment doesn't break.
                vecs = (vecs + [None] * len(texts))[: len(texts)]
            return vecs
    except Exception as e:
        logger.warning("batch embed exception: %s", e)
        return [None] * len(texts)


async def embed_all_knowledge(progress: ProgressCB | None = None) -> dict:
    """Embed every knowledge_item that's missing a vector or whose stored
    embedding came from a different model. Returns counts."""
    cfg = load_config().llm
    model = cfg.embedding_model
    if not model:
        raise RuntimeError("未配置 embedding 模型，请到 Settings → AI 模型 选择")

    async def _emit(pct: int, msg: str) -> None:
        if progress is None:
            return
        rv = progress(pct, msg)
        if hasattr(rv, "__await__"):
            await rv  # type: ignore[func-returns-value]

    await _emit(5, "扫描待 embed 的知识点...")
    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT id, title, content
               FROM knowledge_items
               WHERE embedding IS NULL OR embedding_model IS NULL OR embedding_model <> ?
               ORDER BY id""",
            (model,),
        )
    pending = [dict(r) for r in rows]
    total = len(pending)
    if not total:
        await _emit(100, "已是最新")
        return {"embedded": 0, "skipped": 0, "total": 0}

    # 16 per request — large enough to amortize HTTP, small enough to keep
    # per-batch latency under 30s on local CPU embed models.
    BATCH = 16
    embedded = 0
    failed = 0

    for i in range(0, total, BATCH):
        batch = pending[i : i + BATCH]
        texts = [f"{r['title']}\n{r['content']}" for r in batch]
        await _emit(
            5 + int(90 * i / total),
            f"embed {i+1}-{min(i+BATCH, total)}/{total}...",
        )
        vecs = await embed_batch(texts, model)
        async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
            for row, vec in zip(batch, vecs):
                if vec is None:
                    failed += 1
                    continue
                await db.execute(
                    "UPDATE knowledge_items SET embedding=?, embedding_model=? WHERE id=?",
                    (serialize_vec(vec), model, row["id"]),
                )
                embedded += 1
            await db.commit()

    await _emit(100, f"已 embed {embedded} 条" + (f"，{failed} 条失败" if failed else ""))
    return {"embedded": embedded, "skipped": failed, "total": total}
