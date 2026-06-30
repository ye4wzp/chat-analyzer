import asyncio
import json
from pathlib import Path

import aiosqlite


def run(coro):
    return asyncio.run(coro)


def test_config_update_accepts_full_settings_payload(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.main import ConfigUpdate, update_config

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")

    updated = run(update_config(ConfigUpdate(
        daily_token_budget=12345,
        budget_action="warn",
        vip_contacts=["Alice", "Bob"],
        llm_provider="openai_compatible",
        llm_api_url="http://127.0.0.1:1234/v1",
        llm_model="qwen3",
        llm_api_key="test-key",
    )))

    assert updated["daily_token_budget"] == 12345
    assert updated["budget_action"] == "warn"
    assert updated["vip_contacts"] == ["Alice", "Bob"]
    assert json.loads((tmp_path / "config.json").read_text())["vip_contacts"] == ["Alice", "Bob"]


def test_config_response_redacts_secrets(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core.config import Config, save_config
    from app.api.config import get_config

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")

    cfg = Config()
    cfg.llm.api_key = "llm-secret"
    cfg.qq.token = "qq-token"
    cfg.qq.uin = "1092747679"
    cfg.telegram.api_hash = "telegram-hash"
    cfg.telegram.phone = "+8613800138000"
    cfg.telegram.session_string = "telegram-session"
    save_config(cfg)

    redacted = run(get_config())

    assert redacted["llm"]["api_key"] == "********"
    assert redacted["qq"]["token"] == "********"
    assert redacted["qq"]["uin"] == "********"
    assert redacted["telegram"]["api_hash"] == "********"
    assert redacted["telegram"]["phone"] == "********"
    assert redacted["telegram"]["session_string"] == "********"


def test_vip_contacts_bypass_pre_filter(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core.config import Config, save_config
    from app.services.pre_filter import is_noise

    monkeypatch.setattr(config_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    save_config(Config(vip_contacts=["Alice"]))

    assert is_noise("ok", "text", "Alice") is False


def test_confirm_analysis_saves_knowledge_and_message_analysis(tmp_path, monkeypatch):
    from app.core import database as database_module
    import app.main as main_module
    from app.main import ConfirmRequest, confirm_analysis

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    run(database_module.init_db())

    async def seed_message():
        async with aiosqlite.connect(str(db_path)) as db:
            cursor = await db.execute(
                """INSERT INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "telegram",
                    "chat-1",
                    "Project",
                    "group",
                    "u1",
                    "Alice",
                    "Ship the new parser tomorrow",
                    "text",
                    "2026-04-30T10:00:00",
                    "tg-1",
                    "{}",
                ),
            )
            await db.commit()
            return cursor.lastrowid

    message_id = run(seed_message())
    task_id = "task1"
    main_module._pending_results[task_id] = [{
        "title": "Parser",
        "content": "Ship the new parser tomorrow",
        "tags": ["todo"],
        "source_chat": "Project",
        "source_message_ids": json.dumps([message_id]),
        "urgency": 5,
        "batch_id": "batch1",
    }]

    result = run(confirm_analysis(task_id, ConfirmRequest(ids=[0])))

    async def fetch_counts():
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            knowledge = await db.execute_fetchall("SELECT * FROM knowledge_items")
            analysis = await db.execute_fetchall("SELECT * FROM analysis_results")
            return knowledge, analysis

    knowledge, analysis = run(fetch_counts())
    assert result == {"saved": 1, "skipped": 0}
    assert len(knowledge) == 1
    assert len(analysis) == 1
    assert analysis[0]["message_id"] == message_id
    assert analysis[0]["category"] == "todo"
    assert analysis[0]["urgency"] == 5


def test_confirm_analysis_keeps_pending_results_when_save_fails(tmp_path, monkeypatch):
    from app.core import database as database_module
    import app.main as main_module
    import app.services.knowledge as knowledge_module
    from app.main import ConfirmRequest, confirm_analysis

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    run(database_module.init_db())

    task_id = "task-fail"
    main_module._pending_results[task_id] = [{
        "title": "Parser",
        "content": "Ship the new parser tomorrow",
        "tags": ["todo"],
        "source_chat": "Project",
        "source_message_ids": "[]",
        "urgency": 5,
        "batch_id": "batch1",
    }]

    async def fail_save(_db, _items):
        raise RuntimeError("write failed")

    monkeypatch.setattr(knowledge_module, "save_knowledge_items", fail_save)

    try:
        run(confirm_analysis(task_id, ConfirmRequest(ids=[0])))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected save failure")

    assert task_id in main_module._pending_results


def test_telegram_import_is_idempotent(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.services.sync.telegram import import_telegram_json

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    import app.services.sync.telegram as telegram_module
    monkeypatch.setattr(telegram_module, "DB_PATH", db_path)
    run(database_module.init_db())

    export_path = tmp_path / "result.json"
    export_path.write_text(json.dumps({
        "name": "Project",
        "type": "personal_group",
        "id": 100,
        "messages": [{
            "id": 1,
            "type": "message",
            "date": "2026-04-30T10:00:00",
            "from_id": "user1",
            "from": "Alice",
            "text": "hello",
        }],
    }), encoding="utf-8")

    run(import_telegram_json(str(export_path)))
    run(import_telegram_json(str(export_path)))

    async def count_messages():
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall("SELECT COUNT(*) FROM messages")
            return rows[0][0]

    assert run(count_messages()) == 1


def test_messages_filter_by_platform_and_chat_id_exactly(tmp_path, monkeypatch):
    from app.api.messages import get_messages
    from app.core import database as database_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            for platform, chat_id, source_id in (
                ("wechat", "same-name-1", "wx-1"),
                ("telegram", "same-name-2", "tg-1"),
            ):
                await db.execute(
                    """INSERT INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        platform,
                        chat_id,
                        "Same Name",
                        "group",
                        "u1",
                        "Alice",
                        source_id,
                        "text",
                        "2026-04-30T10:00:00",
                        source_id,
                        "{}",
                    ),
                )
            await db.commit()

    run(seed())

    rows = run(get_messages(platform="wechat", chat_id="same-name-1"))

    assert [r["source_id"] for r in rows] == ["wx-1"]


def test_analyze_uses_recent_messages_in_chronological_order(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config
    from app.core.tasks import create_task
    import app.core.runners as runners_module
    import app.services.analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(runners_module, "load_config", lambda: Config())
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            for idx, ts in enumerate(("2026-04-30T10:00:00", "2026-04-30T12:00:00"), start=1):
                await db.execute(
                    """INSERT INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "telegram",
                        "chat-1",
                        "Project",
                        "group",
                        "u1",
                        "Alice",
                        f"message-{idx}",
                        "text",
                        ts,
                        f"tg-{idx}",
                        "{}",
                    ),
                )
            await db.commit()

    captured: list[str] = []

    class FakeAnalyzer:
        llm_fail_count = 0
        llm_call_count = 0
        last_llm_error = None

        async def analyze_messages(self, messages, on_progress=None):
            captured.extend(m["timestamp"] for m in messages)
            return [], ""

    monkeypatch.setattr(analyzer_module, "AnalyzerService", FakeAnalyzer)
    run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(task_id, runners_module.AnalyzeRequest(full=True, limit=2)))

    assert captured == ["2026-04-30T10:00:00", "2026-04-30T12:00:00"]


def test_incremental_analyze_matches_spaced_source_message_ids(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config
    from app.core.tasks import create_task
    import app.core.runners as runners_module
    import app.services.analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(runners_module, "load_config", lambda: Config())
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            ids = []
            for idx, ts in enumerate((
                "2026-04-30T10:00:00",
                "2026-04-30T11:00:00",
                "2026-04-30T12:00:00",
            ), start=1):
                cursor = await db.execute(
                    """INSERT INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "telegram",
                        "chat-1",
                        "Project",
                        "group",
                        "u1",
                        "Alice",
                        f"message-{idx}",
                        "text",
                        ts,
                        f"tg-{idx}",
                        "{}",
                    ),
                )
                ids.append(cursor.lastrowid)
            await db.execute(
                """INSERT INTO knowledge_items
                   (title, content, source_chat, source_message_ids, tags, batch_id, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    "Existing",
                    "Already analyzed",
                    "Project",
                    json.dumps(ids[:2]),
                    "[]",
                    "batch1",
                    "2026-04-30T11:30:00",
                ),
            )
            await db.commit()
            return ids

    captured: list[int] = []

    class FakeAnalyzer:
        llm_fail_count = 0
        llm_call_count = 0
        last_llm_error = None

        async def analyze_messages(self, messages, on_progress=None):
            captured.extend(m["id"] for m in messages)
            return [], ""

    monkeypatch.setattr(analyzer_module, "AnalyzerService", FakeAnalyzer)
    ids = run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(task_id, runners_module.AnalyzeRequest(limit=10)))

    assert captured == [ids[2]]


def test_single_chat_incremental_uses_chat_scoped_cursor(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config
    from app.core.tasks import create_task
    import app.core.runners as runners_module
    import app.services.analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(runners_module, "load_config", lambda: Config())
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            analyzed = await db.execute(
                """INSERT INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "telegram",
                    "chat-a",
                    "A",
                    "group",
                    "u1",
                    "Alice",
                    "already analyzed elsewhere",
                    "text",
                    "2026-04-30T12:00:00",
                    "tg-a",
                    "{}",
                ),
            )
            target = await db.execute(
                """INSERT INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "telegram",
                    "chat-b",
                    "B",
                    "group",
                    "u2",
                    "Bob",
                    "should be analyzed",
                    "text",
                    "2026-04-30T11:00:00",
                    "tg-b",
                    "{}",
                ),
            )
            await db.execute(
                """INSERT INTO knowledge_items
                   (title, content, source_chat, source_message_ids, tags, batch_id, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    "Existing",
                    "Other chat already analyzed",
                    "A",
                    json.dumps([analyzed.lastrowid]),
                    "[]",
                    "batch1",
                    "2026-04-30T12:30:00",
                ),
            )
            await db.commit()
            return target.lastrowid

    captured: list[int] = []

    class FakeAnalyzer:
        llm_fail_count = 0
        llm_call_count = 0
        last_llm_error = None

        async def analyze_messages(self, messages, on_progress=None):
            captured.extend(m["id"] for m in messages)
            return [], ""

    monkeypatch.setattr(analyzer_module, "AnalyzerService", FakeAnalyzer)
    target_id = run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(
        task_id,
        runners_module.AnalyzeRequest(platform="telegram", chat_id="chat-b", limit=10),
    ))

    assert captured == [target_id]


def test_pending_task_counts_as_running():
    from app.core import tasks as tasks_module

    tasks_module._tasks.clear()
    tasks_module._pending_results.clear()
    task_id = tasks_module.create_task("sync_wechat")

    assert tasks_module._tasks[task_id]["status"] == "pending"
    assert tasks_module.is_task_running("sync_wechat") is True


def test_date_only_until_includes_entire_messages_message(tmp_path, monkeypatch):
    from app.api.messages import get_messages
    from app.api.search import search
    from app.core import database as database_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                """INSERT INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    "telegram",
                    "chat-1",
                    "Project",
                    "group",
                    "u1",
                    "Alice",
                    "same-day message",
                    "text",
                    "2026-04-30T10:00:00",
                    "tg-1",
                    "{}",
                ),
            )
            await db.commit()

    run(seed())

    messages = run(get_messages(until="2026-04-30"))
    results = run(search(keyword="same", until="2026-04-30"))

    assert [m["source_id"] for m in messages] == ["tg-1"]
    assert [r["source_id"] for r in results] == ["tg-1"]


def test_init_db_only_normalizes_candidate_timestamps(tmp_path, monkeypatch):
    from app.core import database as database_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            for source_id, timestamp in (
                ("normalized", "2026-04-30T10:00:00"),
                ("zoned", "2026-04-30T02:00:00Z"),
                ("epoch", 177_000_0000),
            ):
                await db.execute(
                    """INSERT INTO messages
                       (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                        content, msg_type, timestamp, source_id, raw_data)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "telegram",
                        "chat-1",
                        "Project",
                        "group",
                        "u1",
                        "Alice",
                        source_id,
                        "text",
                        timestamp,
                        source_id,
                        "{}",
                    ),
                )
            await db.commit()

    seen: list[object] = []

    def fake_normalize_timestamp(value):
        seen.append(value)
        return "2026-04-30T10:00:00"

    run(seed())
    monkeypatch.setattr(database_module, "normalize_timestamp", fake_normalize_timestamp)
    run(database_module.init_db())

    assert "2026-04-30T10:00:00" not in {str(value) for value in seen}
    assert {"2026-04-30T02:00:00Z", "1770000000"} == {str(value) for value in seen}


def test_qq_import_normalizes_epoch_timestamp(tmp_path, monkeypatch):
    from app.core import database as database_module
    import app.services.sync.qq as qq_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(qq_module, "DB_PATH", db_path)
    run(database_module.init_db())

    export_path = tmp_path / "qq.json"
    export_path.write_text(json.dumps({
        "name": "Project",
        "id": "qq-chat",
        "messages": [{
            "id": 1,
            "timestamp": 177_000_0000,
            "sender_id": "u1",
            "sender_name": "Alice",
            "content": "hello",
        }],
    }), encoding="utf-8")

    run(qq_module.import_qq_json(str(export_path)))

    async def fetch_timestamp():
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall("SELECT timestamp FROM messages")
            return rows[0][0]

    timestamp = run(fetch_timestamp())
    assert "T" in timestamp
    assert timestamp[:4].isdigit()


def test_budget_check_uses_daily_llm_usage(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config
    import app.services.analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(analyzer_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        analyzer_module,
        "load_config",
        lambda: Config(daily_token_budget=100, budget_action="stop"),
    )
    run(database_module.init_db())

    async def seed_usage():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO llm_usage (prompt_tokens, completion_tokens) VALUES (?, ?)",
                (80, 10),
            )
            await db.commit()

    run(seed_usage())

    svc = analyzer_module.AnalyzerService()
    try:
        run(svc._check_budget(20))
    except analyzer_module.TokenBudgetExceeded:
        pass
    else:
        raise AssertionError("expected daily budget to block over-budget call")


def test_contact_tagging_suggest_confirm_reject_flow(tmp_path, monkeypatch):
    from app.api import tags as tags_api
    from app.core import database as database_module
    from app.core import runners
    from app.core.tasks import create_task
    from app.models.tags import ContactTagAdd, LinkIdsRequest, TagCreate
    from app.services import analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(analyzer_module, "DB_PATH", db_path)

    async def fake_call_llm(self, prompt, purpose=""):
        self.llm_call_count += 1
        return '[{"name":"同事","confidence":0.9,"reason":"聊工作"},{"name":"技术圈","confidence":0.8,"reason":"聊代码"}]'

    monkeypatch.setattr(analyzer_module.AnalyzerService, "_call_llm", fake_call_llm)
    run(database_module.init_db())

    async def seed():
        async with aiosqlite.connect(str(db_path)) as db:
            for i in range(5):
                await db.execute(
                    """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id,
                       sender_name, content, msg_type, timestamp, source_id, raw_data)
                       VALUES ('wechat','wxid_a','张三','private','wxid_a','张三',?, 'text',?,?,'{}')""",
                    (f"消息{i}", f"2026-05-0{i + 1}T10:00:00", f"a{i}"),
                )
            # a group chat that must be skipped when include_groups=False
            await db.execute(
                """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id,
                   sender_name, content, msg_type, timestamp, source_id, raw_data)
                   VALUES ('wechat','g@chatroom','群','group','x','x','hi','text','2026-05-01T10:00:00','g','{}')""",
            )
            await db.commit()

    run(seed())
    run(tags_api.create_tag(TagCreate(name="同事")))  # preset → 同事 is_new=False

    tid = create_task("tag")
    run(runners.run_tag_batch(tid, include_groups=False, only_untagged=True, msg_limit=100, max_contacts=None))

    sugg = run(tags_api.list_suggestions())
    assert {s["tag_name"] for s in sugg} == {"同事", "技术圈"}
    assert all(s["chat_id"] != "g@chatroom" for s in sugg)  # group skipped

    # 技术圈 starts pending; confirming its link activates the tag.
    jishu = [s["link_id"] for s in sugg if s["tag_name"] == "技术圈"]
    assert run(tags_api.confirm_links(LinkIdsRequest(link_ids=jishu)))["confirmed"] == 1
    tag_list = run(tags_api.list_tags())
    assert next(t for t in tag_list if t["name"] == "技术圈")["status"] == "active"

    # Rejecting the 同事 suggestion removes only that link (同事 stays as a preset tag).
    tongshi = [s["link_id"] for s in sugg if s["tag_name"] == "同事"]
    assert run(tags_api.reject_links(LinkIdsRequest(link_ids=tongshi)))["rejected"] == 1
    assert run(tags_api.list_suggestions()) == []
    assert any(t["name"] == "同事" for t in run(tags_api.list_tags()))

    # Manual add lands as confirmed.
    run(tags_api.add_contact_tag("wechat", "wxid_a", ContactTagAdd(name="同事")))
    names = {c["name"] for c in run(tags_api.contact_tags("wechat", "wxid_a")) if c["status"] == "confirmed"}
    assert {"同事", "技术圈"} <= names
