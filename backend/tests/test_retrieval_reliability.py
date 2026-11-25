"""Evidence boundaries and stable pagination using synthetic messages only."""
import asyncio
import json
from types import SimpleNamespace

import aiosqlite
import pytest
from fastapi import HTTPException


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core import config, database
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    run(database.init_db())
    async def seed():
        async with aiosqlite.connect(database.DB_PATH) as db:
            for i in range(1, 12):
                await db.execute("""INSERT INTO messages(id,platform,chat_id,chat_name,sender_name,content,timestamp,raw_data)
                    VALUES(?,?,?,?,?,?,?,?)""", (i, "qq" if i < 11 else "telegram", "c", "同名群", "测试", "充值问题 " + str(i), "2026-10-26T12:00:00", '{"private":"hidden"}'))
            await db.commit()
    run(seed())
    return database.DB_PATH


def test_same_second_context_keeps_hit_and_scope(store):
    from app.services.ask import _expand_context, _search_messages
    async def check():
        async with aiosqlite.connect(store) as db:
            db.row_factory = aiosqlite.Row
            scope = dict(platform="qq", chat_id="c", since="2026-10-26", until="2026-10-26")
            hits = await _search_messages(db, ["充值", "充值"], scope)
            assert all(h["platform"] == "qq" and h["matches"] == 1 for h in hits)
            hit = dict((await db.execute_fetchall("SELECT * FROM messages WHERE id=7"))[0])
            context = await _expand_context(db, hit, scope)
            assert [r["id"] for r in context] == [4, 5, 6, 7, 8, 9, 10]
            assert [r["id"] for r in context].count(7) == 1
            assert not await _search_messages(db, ["充值"], dict(platform="qq", until="2026-10-25"))
    run(check())


def test_scoped_knowledge_excludes_mixed_missing_unknown_sources(store, monkeypatch):
    from app.services import ask, embedder
    monkeypatch.setattr(ask, "load_config", lambda: SimpleNamespace(llm=SimpleNamespace(embedding_model="test")))
    async def embed(_): return [1.0]
    monkeypatch.setattr(embedder, "embed_text", embed)
    monkeypatch.setattr(embedder, "rank_by_similarity", lambda vec, blobs, count: [(i, 1.) for i in range(min(len(blobs), count))])
    async def check():
        async with aiosqlite.connect(store) as db:
            db.row_factory = aiosqlite.Row
            for title, ids in [("ok", [1, 2]), ("mixed", [1, 11]), ("missing", [1, 99]), ("unknown", [])]:
                await db.execute("INSERT INTO knowledge_items(title,content,source_message_ids,embedding,embedding_model) VALUES(?,?,?,?,?)", (title, title, json.dumps(ids), b"vec", "test"))
            rows = await ask._search_knowledge(db, "充值", dict(platform="qq", chat_id="c"))
            assert [r["title"] for r in rows] == ["ok"]
    run(check())


def test_knowledge_corrections_and_different_sources_survive(store, monkeypatch):
    from app.services.knowledge import save_knowledge_items, parse_source_message_ids
    from app.api import analyze
    from app.models.analyze import ConfirmRequest
    monkeypatch.setattr(analyze, "spawn_embed_if_needed", lambda: None)
    async def check():
        async with aiosqlite.connect(store) as db:
            item = dict(title="处理办法", content="原建议", source_chat="同名群", source_message_ids="[1]")
            assert await save_knowledge_items(db, [item]) == 1
            assert await save_knowledge_items(db, [item]) == 0
            assert await save_knowledge_items(db, [dict(item, content="后来发现无效")]) == 1
            assert await save_knowledge_items(db, [dict(item, source_message_ids="[11]")]) == 1
            await db.execute("INSERT INTO analysis_batches(task_id,status) VALUES('review','pending')")
            cursor = await db.execute("INSERT INTO pending_items(task_id,title,content,source_message_ids,status) VALUES('review','处理办法','最终解决方案','[1]','pending')")
            pending_id = cursor.lastrowid
            await db.commit()
        result = await analyze.confirm_analysis("review", ConfirmRequest(ids=[pending_id]))
        assert result["saved"] == 1 and result["duplicates"] == 0
    run(check())
    assert parse_source_message_ids("null") == []
    assert parse_source_message_ids('{"1":2}') == []


@pytest.mark.parametrize("route", ["search", "messages"])
def test_cursor_avoids_duplicate_when_new_messages_arrive(store, route):
    from app.api.messages import get_messages
    from app.api.search import search
    endpoint = search if route == "search" else get_messages
    kwargs = {"keyword": "充值"} if route == "search" else {}
    async def check():
        first = await endpoint(platform="qq", chat_id="c", limit=3, **kwargs)
        assert [r["id"] for r in first] == [10, 9, 8]
        async with aiosqlite.connect(store) as db:
            await db.execute("INSERT INTO messages(id,platform,chat_id,content,timestamp) VALUES(12,'qq','c','充值','2026-10-26T12:00:01')")
            await db.commit()
        second = await endpoint(platform="qq", chat_id="c", limit=3, before_ts=first[-1]["timestamp"], before_id=first[-1]["id"], **kwargs)
        assert [r["id"] for r in second] == [7, 6, 5]
        assert all("raw_data" not in r for r in first + second)
        legacy = await endpoint(platform="qq", chat_id="c", limit=2, offset=1, **kwargs)
        assert [r["id"] for r in legacy] == [10, 9]
        with pytest.raises(HTTPException): await endpoint(before_id=8, **kwargs)
    run(check())


def test_source_lookup_is_bounded_and_only_returns_requested_ids(store):
    from app.api.messages import get_messages
    rows = run(get_messages(ids="2,11,99", limit=100))
    assert {r["id"] for r in rows} == {2, 11}
    assert all("raw_data" not in r for r in rows)
    for invalid in ["", "1;DELETE", ",".join(["1"] * 101), "9" * 30]:
        with pytest.raises(HTTPException): run(get_messages(ids=invalid))


def test_invalid_ask_date_is_rejected_before_model_call():
    from pydantic import ValidationError
    from app.models.ask import AskRequest
    with pytest.raises(ValidationError): AskRequest(question='充值', until='2026-99-99')
    assert AskRequest(question='充值', until='2026-10-26').until == '2026-10-26'
