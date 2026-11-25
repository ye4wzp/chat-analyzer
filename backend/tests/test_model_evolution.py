"""Ingest normalisation, sync scope, person layer, id-keyed scope and asset-only backup."""
import asyncio
import json
import sqlite3

import aiosqlite


def run(coro):
    return asyncio.run(coro)


def _setup(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core import database as database_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    return db_path


def test_upsert_classifies_kind_and_applies_raw_policy(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config, save_config
    from app.services.sync._common import upsert_message

    db_path = _setup(tmp_path, monkeypatch)
    run(database_module.init_db())
    save_config(Config())

    async def ingest():
        async with aiosqlite.connect(str(db_path)) as db:
            common = dict(chat_id="g1", chat_name="群", chat_type="group", sender_id="", timestamp="2026-01-01T10:00:00")
            await upsert_message(db, platform="wechat", sender_name="a", content="你好", msg_type="文本", src_id="1", raw={"x": 1}, **common)
            await upsert_message(db, platform="wechat", sender_name="b", content="[图片] l", msg_type="图片", src_id="2", raw={"x": 2}, **common)
            await upsert_message(db, platform="qq", sender_name="c", content="纯文本", msg_type="text", src_id="3", raw={"elements": []}, **common)
            await upsert_message(db, platform="wechat", sender_name="", content='[系统] "a" 撤回了一条消息', msg_type="系统", src_id="4", raw={}, **common)
            await db.commit()

    run(ingest())
    con = sqlite3.connect(db_path)
    rows = con.execute("SELECT platform, kind, raw_data IS NULL, tz_offset IS NOT NULL FROM messages ORDER BY id").fetchall()
    assert rows == [
        ("wechat", "text", 1, 1),    # text on WeChat: raw dropped
        ("wechat", "image", 0, 1),   # non-text: raw kept
        ("qq", "text", 0, 1),        # QQ: always kept
        ("wechat", "system", 0, 1),
    ]


def test_pre_filter_uses_cross_platform_kind(tmp_path, monkeypatch):
    from app.core.config import Config, save_config
    from app.services.pre_filter import filter_messages

    _setup(tmp_path, monkeypatch)
    save_config(Config())
    passed, noise = filter_messages([
        {"platform": "wechat", "msg_type": "系统", "content": '[系统] "x" 撤回了一条消息', "sender_name": ""},
        {"platform": "wechat", "msg_type": "文本", "content": "明天开会讨论方案", "sender_name": "a"},
        {"platform": "wechat", "kind": "sticker", "msg_type": "表情", "content": "[表情]", "sender_name": "a"},
    ])
    assert [m["content"][:4] for m in passed] == ["明天开会"]
    assert len(noise) == 2


def test_sync_scope_gate(tmp_path, monkeypatch):
    from app.core.config import ChatRef, Config, save_config
    from app.services.sync._common import in_sync_scope

    _setup(tmp_path, monkeypatch)
    cfg = Config()
    cfg.sync_scope.exclude_chats = [ChatRef(platform="wechat", chat_id="noisy@chatroom")]
    save_config(cfg)
    assert in_sync_scope("telegram", "-100", "channel") is False       # channels off by default
    assert in_sync_scope("telegram", "-100", "group") is True
    assert in_sync_scope("wechat", "noisy@chatroom", "group") is False
    assert in_sync_scope("wechat", "ok@chatroom", "group") is True
    cfg.sync_scope.exclude_channels = False
    save_config(cfg)
    assert in_sync_scope("telegram", "-100", "channel") is True


def test_aliases_migrate_to_persons_and_vip_resolves(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.services.persons import person_for_chat, vip_identity_values

    db_path = _setup(tmp_path, monkeypatch)
    # Pre-create the legacy table as an old install would have it.
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE contact_aliases (id INTEGER PRIMARY KEY AUTOINCREMENT, canonical_name TEXT NOT NULL,
            platform TEXT NOT NULL, platform_id TEXT NOT NULL, platform_name TEXT, created_at DATETIME,
            UNIQUE(platform, platform_id));
        INSERT INTO contact_aliases (canonical_name, platform, platform_id, platform_name) VALUES
            ('张三', 'wechat', 'wxid_zs', '三哥'), ('张三', 'telegram', '1001', 'zhang'), ('李四', 'qq', 'u_ls', NULL);
    """)
    con.commit(); con.close()
    run(database_module.init_db())

    con = sqlite3.connect(db_path)
    assert con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='contact_aliases'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM persons").fetchone()[0] == 2
    assert con.execute("SELECT COUNT(*) FROM person_identities WHERE source='migrated'").fetchone()[0] == 3
    con.close()

    async def check():
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            p = await person_for_chat(db, "telegram", "1001")
            vals = await vip_identity_values(db, ["张三"])
            return p, vals
    p, vals = run(check())
    assert p["name"] == "张三"
    assert vals == {"wxid_zs", "三哥", "1001", "zhang"}


def test_person_api_link_footprint_and_suggestions(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.api import persons as api
    from app.api import contacts as contacts_api
    from app.services import stats as stats_module

    db_path = _setup(tmp_path, monkeypatch)
    run(database_module.init_db())
    con = sqlite3.connect(db_path)
    rows = [
        ("wechat", "wxid_a", "阿明", "private", "", "阿明", "在吗", "文本", "text", "2026-01-01T09:00:00", "1"),
        ("wechat", "wxid_a", "阿明", "private", "", "子非鱼", "在", "文本", "text", "2026-01-01T09:01:00", "2"),
        ("wechat", "g1@chatroom", "群A", "group", "", "阿明", "大家好", "文本", "text", "2026-01-02T09:00:00", "3"),
        ("wechat", "g2@chatroom", "群B", "group", "", "阿明", "哈哈", "文本", "text", "2026-02-02T09:00:00", "4"),
        ("wechat", "g2@chatroom", "群B", "group", "", "小红", "嗯", "文本", "text", "2026-02-02T09:05:00", "5"),
    ]
    con.executemany(
        """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id, sender_name, content, msg_type, kind, timestamp, source_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows,
    )
    con.commit(); con.close()
    run(stats_module.refresh())

    # Before linking: no person, one candidate (group nickname == chat name).
    before = run(api.person_for_chat("wechat", "wxid_a"))
    assert before["person"] is None
    assert [c["value"] for c in before["suggestions"]] == ["阿明"] and before["suggestions"][0]["groups"] == 2

    linked = run(api.link_chat(api.LinkChatRequest(
        name="阿明", platform="wechat", chat_id="wxid_a", chat_name="阿明",
        senders=[api.IdentityCreate(platform="wechat", kind="sender", value="阿明", display_name="阿明")],
    )))
    detail = run(api.get_person(linked["id"]))
    assert detail["name"] == "阿明"
    assert [c["chat_id"] for c in detail["chats"]] == ["wxid_a"]
    assert sorted(g["chat_name"] for g in detail["groups"]) == ["群A", "群B"]
    assert detail["total_messages"] == 2 + 2  # private chat rows + group rows

    profile = run(contacts_api.contact_profile("wechat", "wxid_a"))
    assert profile["person"]["name"] == "阿明" and profile["footprint"]["total_messages"] == 4
    assert profile["identity_candidates"][0]["already_linked"] is True

    listed = run(contacts_api.list_contacts(chat_type="private"))
    assert listed[0]["person_id"] == linked["id"] and listed[0]["canonical_name"] == "阿明"

    # Merge into another person and prune.
    other = run(api.create_person(api.PersonCreate(name="明哥")))
    run(api.merge_person(linked["id"], api.MergeRequest(into=other["id"])))
    assert run(api.get_person(other["id"]))["total_messages"] == 4
    assert len(run(api.list_persons())) == 1


def test_chat_filter_refs_and_name_migration(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import ChatFilterConfig, ChatRef, Config, load_config, migrate_chat_filter_names, save_config
    from app.core.runners import chat_filter_clause
    from app.services import stats as stats_module

    db_path = _setup(tmp_path, monkeypatch)
    run(database_module.init_db())
    con = sqlite3.connect(db_path)
    con.executemany(
        """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id, sender_name, content, msg_type, kind, timestamp, source_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("wechat", "g1@chatroom", "项目群", "group", "", "a", "x", "文本", "text", "2026-01-01T09:00:00", "1"),
            ("qq", "123", "项目群", "group", "", "a", "x", "text", "text", "2026-01-01T09:00:00", "2"),   # same name → ambiguous
            ("wechat", "g3@chatroom", "读书会", "group", "", "a", "x", "文本", "text", "2026-01-01T09:00:00", "3"),
        ],
    )
    con.commit(); con.close()
    run(stats_module.refresh())

    save_config(Config(chat_filter=ChatFilterConfig(mode="whitelist", chats=["读书会", "项目群", "不存在的群"])))
    moved = run(migrate_chat_filter_names())
    cfg = load_config()
    assert moved == 1
    assert [r.chat_id for r in cfg.chat_filter.refs] == ["g3@chatroom"]
    assert cfg.chat_filter.chats == ["项目群", "不存在的群"]   # ambiguous / unknown stay as names

    clause, params = chat_filter_clause(cfg.chat_filter, alias="m")
    assert clause == "(((m.platform = ? AND m.chat_id = ?)) OR m.chat_name IN (?,?))"
    assert params == ["wechat", "g3@chatroom", "项目群", "不存在的群"]
    cfg.chat_filter.mode = "blacklist"
    assert chat_filter_clause(cfg.chat_filter)[0].startswith("NOT (")
    assert chat_filter_clause(ChatFilterConfig())[0] == ""


def test_backup_copies_assets_only(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.services import backup, stats as stats_module

    db_path = _setup(tmp_path, monkeypatch)
    run(database_module.init_db())
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id, sender_name, content, msg_type, kind, timestamp, source_id)
           VALUES ('wechat','c','A','private','','A','看 https://x.io','文本','text','2026-01-01T10:00:00','1')"""
    )
    con.commit(); con.close()
    run(stats_module.refresh())

    out = tmp_path / "bak.db"
    counts = backup.copy_assets(db_path, out)
    assert counts["messages"] == 1
    names = {r[0] for r in sqlite3.connect(out).execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "messages" in names and "knowledge_items" in names and "persons" in names
    assert not any(n.startswith("stats_") or n == "resources" or n.startswith("messages_fts") for n in names)

    # Restoring the backup: derived tables come back and the stats rebuild from scratch.
    monkeypatch.setattr(database_module, "DB_PATH", out)
    run(database_module.init_db())
    assert run(stats_module.get_status())["ready"] is False
    assert run(stats_module.refresh())["processed"] == 1
    assert run(stats_module.get_status())["ready"] is True
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM resources").fetchone()[0] == 1


def test_knowledge_source_backfill_resolves_ids(tmp_path, monkeypatch):
    from app.core import database as database_module

    db_path = _setup(tmp_path, monkeypatch)
    run(database_module.init_db())
    con = sqlite3.connect(db_path)
    con.execute(
        """INSERT INTO messages (id, platform, chat_id, chat_name, chat_type, sender_id, sender_name, content, msg_type, timestamp, source_id)
           VALUES (42,'wechat','g1@chatroom','老名字','group','','a','x','文本','2026-01-01T10:00:00','1')"""
    )
    con.execute("INSERT INTO knowledge_items (title, content, source_chat, source_message_ids) VALUES ('t','c','老名字', ?)", (json.dumps([42]),))
    con.execute("INSERT INTO knowledge_items (title, content, source_chat, source_message_ids) VALUES ('t2','c','老名字', '[]')")
    con.commit(); con.close()
    run(database_module.init_db())  # migration pass
    rows = sqlite3.connect(db_path).execute("SELECT source_platform, source_chat_id FROM knowledge_items ORDER BY id").fetchall()
    assert rows == [("wechat", "g1@chatroom"), ("wechat", "g1@chatroom")]
