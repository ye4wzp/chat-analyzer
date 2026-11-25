import asyncio
import sqlite3

import pytest

from app.core import database
from app.core.config import ChatRef, Config
from app.services.sync import qq_local


@pytest.fixture
def setup(tmp_path, monkeypatch):
    src = tmp_path / "nt_msg.db"
    with sqlite3.connect(src) as db:
        for table in qq_local.TABLES.values():
            db.execute(f"""CREATE TABLE {table} ([40001] INTEGER,[40050] INTEGER,
                           [40020] TEXT,[40090] TEXT,[40021] TEXT,[40030] INTEGER,[40800] BLOB)""")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "chat.db")
    monkeypatch.setattr(qq_local, "PAGE_SIZE", 1)
    asyncio.run(database.init_db())
    cfg = Config()
    cfg.qq.enabled = True
    cfg.qq.source = "local"
    cfg.qq.local_path = str(src)
    cfg.qq.local_account = "example-a"
    return src, cfg


def add(src, kind, msg_id, ts):
    content = "重复正文".encode()
    blob = bytes([10, len(content)]) + content
    with sqlite3.connect(src) as db:
        db.execute(f"INSERT INTO {qq_local.TABLES[kind]} VALUES(?,?,'uid','昵称','peer',123,?)", (msg_id, ts, blob))


def test_local_sync_same_second_late_arrival_and_repair(setup):
    src, cfg = setup
    ts = 1700000000
    for mid in [-10, 1, 2, 3, 4]:
        add(src, "group", mid, ts)
    add(src, "private", 1, ts)
    run = lambda **kw: asyncio.run(qq_local.sync_local(cfg, **kw))
    assert run()["imported"] == 6
    assert run()["imported"] == 0
    add(src, "group", -20, ts)
    add(src, "private", 2, ts + 1)
    add(src, "group", 10, ts - 90000)
    assert run()["imported"] == 2
    assert run(mode="repair")["imported"] == 1
    assert run(mode="repair")["imported"] == 0
    with sqlite3.connect(database.DB_PATH) as db:
        assert db.execute("SELECT DISTINCT content FROM messages").fetchall() == [("重复正文",)]
    cfg.qq.local_account = "example-b"
    cfg.sync_scope.exclude_chats = [ChatRef(platform="qq", chat_id="private:123")]
    skipped = run(chat_ids=["private:123"])
    assert skipped["excluded"] == 1 and skipped["details"][0]["status"] == "excluded"
    assert run()["imported"] == 7
    assert run()["imported"] == 0


def test_committed_page_survives_interruption(setup):
    src, cfg = setup
    for mid in range(3):
        add(src, "group", mid + 1, 1700000000)
    with sqlite3.connect(database.DB_PATH) as db:
        db.execute("""CREATE TRIGGER interrupt BEFORE INSERT ON messages WHEN NEW.source_id='2'
                      BEGIN SELECT RAISE(ABORT,'simulated disk failure'); END""")
    failed = asyncio.run(qq_local.sync_local(cfg))
    assert failed["imported"] == 1 and failed["details"][0]["status"] == "failed"
    with sqlite3.connect(database.DB_PATH) as db:
        assert db.execute("SELECT msg_id FROM qq_local_cursor").fetchone() == (1,)
        db.execute("DROP TRIGGER interrupt")
    assert asyncio.run(qq_local.sync_local(cfg, chat_ids=["123"]))["imported"] == 2
    assert asyncio.run(qq_local.sync_local(cfg))["imported"] == 0


def test_invalid_source_never_created_or_reported_connected(setup):
    src, cfg = setup
    assert asyncio.run(qq_local.test_source(cfg.qq))["ok"]
    src.unlink()
    with pytest.raises(qq_local.LocalSourceError, match="找不到"):
        asyncio.run(qq_local.test_source(cfg.qq))
    assert not src.exists()
    src.write_bytes(b"opaque file")
    with pytest.raises(qq_local.LocalSourceError, match="明文"):
        asyncio.run(qq_local.test_source(cfg.qq))
