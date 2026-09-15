"""Conversational retrieval (RAG) over messages + knowledge base.

Messages carry no stored vectors (deliberate — see triggers.py), so message
retrieval goes through the FTS5 trigram index with LLM-extracted keywords,
each hit expanded with its surrounding conversation. Knowledge items ARE
embedded, so that channel is semantic. Both are numbered into the prompt and
the answer cites them as 【1】 / 【K1】.
"""
import json
import logging
import re

import aiosqlite

from app.core import database
from app.core.config import load_config

logger = logging.getLogger("app.ask")

MAX_KEYWORDS = 6
HITS_PER_KEYWORD = 10
MAX_EXCERPTS = 8
CONTEXT_BEFORE = 3
CONTEXT_AFTER = 3
KNOWLEDGE_TOP_K = 4
MAX_HISTORY_TURNS = 3
MAX_MSG_CHARS = 200

KEYWORD_PROMPT = """从用户的问题中提取用于全文检索聊天记录的关键词。

要求：
- 返回 2-6 个关键词，每个 2-8 个字
- 关键词应是可能出现在聊天原文里的词（人名、事物、地点、动作），不要抽象概括词
- 可包含常见同义说法（如问"装修"可加"翻新"）
- 仅返回 JSON 字符串数组，如 ["装修","报价"]，不要其他内容

用户的问题：{question}"""

ANSWER_PROMPT = """你是用户的聊天记录助手。根据下面检索到的聊天片段和知识库条目回答用户的问题。

要求：
- 只依据提供的资料回答；资料不足以回答时明确说明"聊天记录中没有找到相关信息"，不要编造
- 在相关陈述后标注来源编号，如【1】【K2】
- 与问题无关的片段直接忽略
- 用简洁的中文回答
{history_block}
用户的问题：{question}

聊天片段（">" 标记命中消息）：
{excerpts}

知识库条目：
{knowledge}"""

HISTORY_BLOCK = """
此前的对话（供理解追问指代）：
{turns}
"""


def _parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(k).strip() for k in parsed if str(k).strip()][:MAX_KEYWORDS]


def _fallback_keywords(question: str) -> list[str]:
    tokens = [t for t in re.split(r"[^\w一-鿿]+", question) if 2 <= len(t) <= 12]
    seen: dict[str, None] = dict.fromkeys(tokens)
    return list(seen)[:MAX_KEYWORDS]


async def _keyword_hits(db: aiosqlite.Connection, kw: str) -> list:
    base = """SELECT id, platform, chat_id, chat_name, sender_name, content, timestamp
              FROM messages WHERE {match} ORDER BY timestamp DESC LIMIT ?"""
    if database.FTS_ENABLED and len(kw) >= 3:
        try:
            return await db.execute_fetchall(
                base.format(match="id IN (SELECT rowid FROM messages_fts WHERE messages_fts MATCH ?)"),
                ('"' + kw.replace('"', '""') + '"', HITS_PER_KEYWORD),
            )
        except aiosqlite.OperationalError:
            pass
    return await db.execute_fetchall(
        base.format(match="content LIKE ?"), (f"%{kw}%", HITS_PER_KEYWORD)
    )


async def _search_messages(db: aiosqlite.Connection, keywords: list[str]) -> list[dict]:
    """Hits ranked by how many keywords they match, then recency."""
    hits: dict[int, dict] = {}
    for kw in keywords:
        for r in await _keyword_hits(db, kw):
            h = hits.setdefault(int(r["id"]), {**dict(r), "matches": 0})
            h["matches"] += 1
    ranked = sorted(hits.values(), key=lambda h: (h["matches"], h["timestamp"] or ""), reverse=True)
    return ranked[:MAX_EXCERPTS]


async def _expand_context(db: aiosqlite.Connection, hit: dict) -> list[dict]:
    """Hit message plus its conversational neighbours, oldest first."""
    cols = "id, sender_name, content, timestamp"
    before = await db.execute_fetchall(
        f"""SELECT {cols} FROM messages WHERE platform=? AND chat_id=? AND timestamp <= ?
            ORDER BY timestamp DESC, id DESC LIMIT ?""",
        (hit["platform"], hit["chat_id"], hit["timestamp"], CONTEXT_BEFORE + 1),
    )
    after = await db.execute_fetchall(
        f"""SELECT {cols} FROM messages WHERE platform=? AND chat_id=? AND timestamp > ?
            ORDER BY timestamp ASC, id ASC LIMIT ?""",
        (hit["platform"], hit["chat_id"], hit["timestamp"], CONTEXT_AFTER),
    )
    return [dict(r) for r in (*reversed(before), *after)]


async def _search_knowledge(db: aiosqlite.Connection, question: str) -> list[dict]:
    cfg = load_config().llm
    if not cfg.embedding_model:
        return []
    from app.services.embedder import embed_text, rank_by_similarity

    vec = await embed_text(question)
    if not vec:
        return []
    rows = await db.execute_fetchall(
        "SELECT id, title, content, embedding FROM knowledge_items"
        " WHERE embedding IS NOT NULL AND embedding_model = ?",
        (cfg.embedding_model,),
    )
    ranked = rank_by_similarity(vec, [r["embedding"] for r in rows], KNOWLEDGE_TOP_K)
    return [
        {"id": rows[i]["id"], "title": rows[i]["title"],
         "content": rows[i]["content"], "similarity": round(score, 4)}
        for i, score in ranked
    ]


def _clip(text: str | None, limit: int = MAX_MSG_CHARS) -> str:
    text = (text or "").strip()
    return text[:limit] + "…" if len(text) > limit else text


def _format_excerpt(ref: int, hit: dict, context: list[dict]) -> str:
    lines = [f"【{ref}】{hit['chat_name'] or hit['chat_id']} · {hit['timestamp']}"]
    for m in context:
        marker = ">" if m["id"] == hit["id"] else " "
        lines.append(f"{marker} {m['sender_name'] or '?'}: {_clip(m['content'])}")
    return "\n".join(lines)


def _history_block(history: list[dict]) -> str:
    if not history:
        return ""
    turns = "\n".join(
        f"问：{t['question']}\n答：{_clip(t['answer'], 400)}"
        for t in history[-MAX_HISTORY_TURNS:]
    )
    return HISTORY_BLOCK.format(turns=turns)


async def answer_question(question: str, history: list[dict]) -> dict:
    from app.services.analyzer import AnalyzerService

    svc = AnalyzerService()
    keywords = _parse_keywords(
        await svc._call_llm(KEYWORD_PROMPT.format(question=question), purpose="ask")
    ) or _fallback_keywords(question)
    logger.info("ask keywords=%s", keywords)

    async with database.connect_db() as db:
        hits = await _search_messages(db, keywords)
        excerpts = [(h, await _expand_context(db, h)) for h in hits]
        knowledge = await _search_knowledge(db, question)

    sources = [
        {"ref": i, "message_id": h["id"], "platform": h["platform"], "chat_id": h["chat_id"],
         "chat_name": h["chat_name"], "sender_name": h["sender_name"],
         "timestamp": h["timestamp"], "preview": _clip(h["content"], 120),
         "content": _clip(h["content"], 1000)}
        for i, (h, _) in enumerate(excerpts, 1)
    ]
    k_sources = [
        {"ref": f"K{j}", "id": k["id"], "title": k["title"],
         "preview": _clip(k["content"], 120), "similarity": k["similarity"]}
        for j, k in enumerate(knowledge, 1)
    ]

    if not excerpts and not knowledge:
        return {
            "answer": "聊天记录和知识库里都没有检索到相关内容。可以换个说法，或补充人名、群名等具体线索再问一次。",
            "sources": [], "knowledge": [], "keywords": keywords,
        }

    prompt = ANSWER_PROMPT.format(
        history_block=_history_block(history),
        question=question,
        excerpts="\n\n".join(_format_excerpt(i, h, ctx) for i, (h, ctx) in enumerate(excerpts, 1)) or "（无）",
        knowledge="\n\n".join(
            f"【K{j}】{k['title']}\n{_clip(k['content'], 400)}" for j, k in enumerate(knowledge, 1)
        ) or "（无）",
    )
    answer = await svc._call_llm(prompt, purpose="ask")
    if answer is None:
        raise RuntimeError(f"LLM 调用失败：{svc.last_llm_error or '未知错误'}")

    return {"answer": answer, "sources": sources, "knowledge": k_sources, "keywords": keywords}
