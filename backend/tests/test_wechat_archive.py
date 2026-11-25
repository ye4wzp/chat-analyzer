import asyncio
import sqlite3

import aiosqlite
import pytest
from fastapi import HTTPException

from app.api.wechat_archive import AccountAction, archive_status, update_accounts
from app.core import database
from app.services import wechat_archive as worker
from app.services.wechat_articles import WechatBusy


def push(chat, *articles):
    content = "\n".join("https://mp.weixin.qq.com/s/" + a for a in articles)
    with sqlite3.connect(database.DB_PATH) as db:
        db.execute("INSERT INTO messages(platform,chat_id,content,timestamp) VALUES('wechat',?,?,'2026-11-08')", (chat, content))


async def drain(fn):
    async with aiosqlite.connect(database.DB_PATH) as db:
        while await fn(db):
            pass


def action(name, *ids):
    return update_accounts(AccountAction(action=name, chat_ids=list(ids)))


def test_selected_backfill_restart_future_and_pause(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "chat.db")
    calls = []

    async def fetch(url):
        calls.append(url.rsplit("/", 1)[1])
        return "全文"

    monkeypatch.setattr(worker, "fetch_body", fetch)

    async def scenario():
        await database.init_db()
        push("gh_a", "shared", "one")
        push("gh_b", "shared")
        push("gh_c", "unselected")
        with pytest.raises(HTTPException):
            await action("enable", "gh_a", "gh_missing")
        assert not (await archive_status())["accounts"]
        await action("enable", "gh_a", "gh_b")
        await drain(worker.scan_batch)
        await database.init_db()  # reopen existing durable state
        await action("enable", "gh_a")
        await drain(worker.fetch_next)
        assert sorted(calls) == ["one", "shared"]
        push("gh_b", "new")
        await action("pause", "gh_b")
        await drain(worker.scan_batch)
        await drain(worker.fetch_next)
        assert "new" not in calls
        await action("enable", "gh_b")
        await drain(worker.scan_batch)
        await drain(worker.fetch_next)
        assert calls[-1] == "new"
        assert [r["saved"] for r in (await archive_status())["accounts"]] == [2, 2]
    asyncio.run(scenario())


def test_verification_retains_failed_item_and_respects_cooldown(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "chat.db")
    async def blocked(url):
        raise WechatBusy("微信要求安全验证")
    monkeypatch.setattr(worker, "fetch_body", blocked)
    async def scenario():
        await database.init_db()
        push("gh_a", "first", "second")
        await action("enable", "gh_a")
        await drain(worker.scan_batch)
        await drain(worker.fetch_next)
        status = await archive_status()
        a = status["accounts"][0]
        assert (a["pending"], a["saved"], a["failed"]) == (1, 0, 1)
        assert a["errors"][0]["reason"] == "微信要求安全验证"
        assert status["cooldown_until"]
        await action("retry", "gh_a")
        await drain(worker.fetch_next)
        a = (await archive_status())["accounts"][0]
        assert (a["pending"], a["failed"]) == (2, 0)
    asyncio.run(scenario())
