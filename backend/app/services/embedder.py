"""Embedding index for knowledge_items.

Vectors are L2-normalized float32 BLOBs. Ranking loads candidate BLOBs into a
numpy matrix and dots against the query — legacy unnormalized rows are
re-normalized at read time so scores stay correct either way. Every embedding
HTTP call is budget-reserved and recorded to llm_usage (purpose='embed').
"""
import logging
from typing import Awaitable, Callable

import aiosqlite
import httpx
import numpy as np

from app.core.config import load_config
from app.core.database import DB_PATH
from app.services import budget

logger = logging.getLogger("app.embedder")

ProgressCB = Callable[[int, str], Awaitable[None] | None]

SEMANTIC_DUP_THRESHOLD = 0.92


def serialize_vec(vec: list[float]) -> bytes:
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tobytes()


def deserialize_vec(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


def cosine(a: list[float], b: list[float]) -> float:
    """Plain cosine similarity. Returns 0 for zero-vectors instead of NaN."""
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    if va.shape != vb.shape:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0 or nb == 0:
        return 0.0
    return float(va @ vb) / (na * nb)


def rank_by_similarity(query_vec: list[float], blobs: list[bytes], top_k: int) -> list[tuple[int, float]]:
    """Rank stored vectors against a query. Returns [(blob_index, score)] for
    the top_k most similar, descending."""
    q = np.asarray(query_vec, dtype=np.float32)
    qn = float(np.linalg.norm(q))
    if qn == 0:
        return []
    q = q / qn

    idx_map: list[int] = []
    rows: list[np.ndarray] = []
    for i, blob in enumerate(blobs):
        v = np.frombuffer(blob, dtype=np.float32)
        if v.shape == q.shape:
            idx_map.append(i)
            rows.append(v)
    if not rows:
        return []

    matrix = np.vstack(rows)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0] = 1.0
    scores = (matrix @ q) / norms
    order = np.argsort(-scores)[:top_k]
    return [(idx_map[int(i)], float(scores[int(i)])) for i in order]


async def embed_text(text: str, model: str | None = None, task_id: str | None = None) -> list[float] | None:
    """One-off embed of a single string. Used by semantic search at query time."""
    cfg = load_config().llm
    model = model or cfg.embedding_model
    if not model:
        return None
    estimate = len(text) // 3
    try:
        await budget.reserve(estimate)
    except budget.TokenBudgetExceeded as e:
        logger.warning("embed skipped: %s", e)
        return None
    try:
        api_url = cfg.api_url.replace("localhost", "127.0.0.1")
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
            usage = data.get("usage") or {}
            await budget.record(
                "embedding", model, int(usage.get("prompt_tokens") or estimate), 0, "embed", task_id,
            )
            return data["data"][0]["embedding"]
    except Exception as e:
        logger.warning("embed call exception: %s", e)
        return None
    finally:
        budget.release(estimate)


async def embed_batch(texts: list[str], model: str, task_id: str | None = None) -> list[list[float] | None]:
    """Embed a batch in one HTTP call where the backend supports it. Falls back
    to per-text calls on failure so one bad row doesn't kill a 500-item run."""
    cfg = load_config().llm
    api_url = cfg.api_url.replace("localhost", "127.0.0.1")
    estimate = sum(len(t) for t in texts) // 3
    try:
        await budget.reserve(estimate)
    except budget.TokenBudgetExceeded as e:
        logger.warning("batch embed skipped: %s", e)
        return [None] * len(texts)
    # `fell_back` lets us release the batch reservation BEFORE the per-text
    # fallback re-reserves each item — otherwise the estimate is counted twice
    # in-flight and can trip a false TokenBudgetExceeded near the daily cap.
    fell_back = False
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{api_url}/embeddings",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={"model": model, "input": texts},
            )
            if resp.status_code != 200:
                logger.warning("batch embed %s failed: %s %s", model, resp.status_code, resp.text[:200])
                fell_back = True
            else:
                data = resp.json()
                # Server may return entries out of order; sort by `index` to be safe.
                entries = sorted(data.get("data", []), key=lambda e: e.get("index", 0))
                vecs = [e["embedding"] for e in entries]
                if len(vecs) != len(texts):
                    logger.warning("batch embed length mismatch: in=%d out=%d", len(texts), len(vecs))
                    fell_back = True
                else:
                    usage = data.get("usage") or {}
                    await budget.record(
                        "embedding", model, int(usage.get("prompt_tokens") or estimate), 0, "embed", task_id,
                    )
                    return vecs
    except Exception as e:
        logger.warning("batch embed exception: %s", e)
        fell_back = True
    finally:
        budget.release(estimate)

    if fell_back:
        return await _embed_each(texts, model, task_id)
    return [None] * len(texts)


async def _embed_each(texts: list[str], model: str, task_id: str | None) -> list[list[float] | None]:
    return [await embed_text(t, model, task_id) for t in texts]


async def embed_all_knowledge(progress: ProgressCB | None = None, task_id: str | None = None) -> dict:
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
        return {"embedded": 0, "failed": 0, "total": 0}

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
        vecs = await embed_batch(texts, model, task_id)
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
    return {"embedded": embedded, "failed": failed, "total": total}


async def find_semantic_duplicates(items: list[dict], task_id: str | None = None) -> set[int]:
    """Indices of `items` that near-duplicate stored knowledge or an earlier
    item in the same batch (cosine >= SEMANTIC_DUP_THRESHOLD). Empty set when
    no embedding model is configured or embedding fails — dedup then falls back
    to the exact matching in save_knowledge_items."""
    cfg = load_config().llm
    model = cfg.embedding_model
    if not model or not items:
        return set()

    vecs = await embed_batch([f"{it.get('title', '')}\n{it.get('content', '')}" for it in items], model, task_id)

    async with aiosqlite.connect(str(DB_PATH), timeout=60) as db:
        rows = await db.execute_fetchall(
            "SELECT embedding FROM knowledge_items WHERE embedding IS NOT NULL AND embedding_model = ?",
            (model,),
        )

    dim = next((len(v) for v in vecs if v is not None), None)
    if dim is None:
        return set()

    # Parse the stored vectors into one row-normalized matrix ONCE, then score
    # each new item with a single matrix-vector product — instead of re-parsing
    # and re-stacking every blob for every item.
    stored = [np.frombuffer(row[0], dtype=np.float32) for row in rows]
    stored = [v for v in stored if v.shape == (dim,)]
    matrix = None
    if stored:
        matrix = np.vstack(stored)
        norms = np.linalg.norm(matrix, axis=1)
        norms[norms == 0] = 1.0
        matrix = matrix / norms[:, None]

    duplicates: set[int] = set()
    kept: list[np.ndarray] = []
    for i, vec in enumerate(vecs):
        if vec is None:
            continue
        q = np.asarray(vec, dtype=np.float32)
        qn = float(np.linalg.norm(q))
        if qn == 0:
            continue
        q = q / qn
        if matrix is not None and float((matrix @ q).max()) >= SEMANTIC_DUP_THRESHOLD:
            duplicates.add(i)
            continue
        if any(float(q @ k) >= SEMANTIC_DUP_THRESHOLD for k in kept):
            duplicates.add(i)
            continue
        kept.append(q)
    return duplicates
