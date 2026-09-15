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


async def seed_pending_batch(db_path, task_id, items):
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO analysis_batches (task_id, summary, status) VALUES (?,?, 'pending')",
            (task_id, "summary"),
        )
        row_ids = []
        for item in items:
            cursor = await db.execute(
                """INSERT INTO pending_items
                   (task_id, title, content, category, urgency, tags, source_chat, source_message_ids, batch_id)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    item["title"],
                    item["content"],
                    item.get("category", ""),
                    item.get("urgency", 3),
                    json.dumps(item.get("tags", []), ensure_ascii=False),
                    item.get("source_chat", ""),
                    item.get("source_message_ids", "[]"),
                    item.get("batch_id", ""),
                ),
            )
            row_ids.append(cursor.lastrowid)
        await db.commit()
        return row_ids


def test_confirm_analysis_saves_knowledge_and_message_analysis(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core import database as database_module
    import app.main as main_module
    from app.main import ConfirmRequest, confirm_analysis

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
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
    row_ids = run(seed_pending_batch(db_path, task_id, [{
        "title": "Parser",
        "content": "Ship the new parser tomorrow",
        "tags": ["todo"],
        "source_chat": "Project",
        "source_message_ids": json.dumps([message_id]),
        "urgency": 5,
        "batch_id": "batch1",
    }]))

    result = run(confirm_analysis(task_id, ConfirmRequest(ids=row_ids)))

    async def fetch_counts():
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            knowledge = await db.execute_fetchall("SELECT * FROM knowledge_items")
            analysis = await db.execute_fetchall("SELECT * FROM analysis_results")
            batch = await db.execute_fetchall("SELECT status FROM analysis_batches WHERE task_id='task1'")
            return knowledge, analysis, batch

    knowledge, analysis, batch = run(fetch_counts())
    assert result["saved"] == 1
    assert result["skipped"] == 0
    assert len(knowledge) == 1
    assert len(analysis) == 1
    assert analysis[0]["message_id"] == message_id
    assert analysis[0]["category"] == "todo"
    assert analysis[0]["urgency"] == 5
    assert batch[0]["status"] == "confirmed"


def test_confirm_analysis_keeps_pending_items_when_save_fails(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core import database as database_module
    import app.main as main_module
    import app.services.knowledge as knowledge_module
    from app.main import ConfirmRequest, confirm_analysis

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(main_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    run(database_module.init_db())

    task_id = "task-fail"
    row_ids = run(seed_pending_batch(db_path, task_id, [{
        "title": "Parser",
        "content": "Ship the new parser tomorrow",
        "tags": ["todo"],
        "source_chat": "Project",
        "source_message_ids": "[]",
        "urgency": 5,
        "batch_id": "batch1",
    }]))

    async def fail_save(_db, _items):
        raise RuntimeError("write failed")

    monkeypatch.setattr(knowledge_module, "save_knowledge_items", fail_save)

    try:
        run(confirm_analysis(task_id, ConfirmRequest(ids=row_ids)))
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected save failure")

    async def pending_status():
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall(
                "SELECT status FROM pending_items WHERE task_id=?", (task_id,)
            )
            return [r[0] for r in rows]

    assert run(pending_status()) == ["pending"]


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

        async def analyze_messages(self, messages, on_progress=None, vip_extra=frozenset()):
            captured.extend(m["timestamp"] for m in messages)
            return [], "", True

    monkeypatch.setattr(analyzer_module, "AnalyzerService", FakeAnalyzer)
    run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(task_id, runners_module.AnalyzeRequest(full=True, limit=2)))

    assert captured == ["2026-04-30T10:00:00", "2026-04-30T12:00:00"]


async def _insert_message(db, chat_id, chat_name, content, ts, source_id, platform="telegram"):
    cursor = await db.execute(
        """INSERT INTO messages
           (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
            content, msg_type, timestamp, source_id, raw_data)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (platform, chat_id, chat_name, "group", "u1", "Alice", content, "text", ts, source_id, "{}"),
    )
    return cursor.lastrowid


def _fake_analyzer(captured: list):
    class FakeAnalyzer:
        llm_fail_count = 0
        llm_call_count = 0
        last_llm_error = None

        async def analyze_messages(self, messages, on_progress=None, vip_extra=frozenset()):
            captured.extend(m["id"] for m in messages)
            return [], "", True

    return FakeAnalyzer


def test_incremental_analyze_uses_analysis_state_cursor(tmp_path, monkeypatch):
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
                ids.append(await _insert_message(db, "chat-1", "Project", f"message-{idx}", ts, f"tg-{idx}"))
            await db.execute(
                "INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts) VALUES ('telegram','chat-1','2026-04-30T11:00:00')"
            )
            await db.commit()
            return ids

    captured: list[int] = []
    monkeypatch.setattr(analyzer_module, "AnalyzerService", _fake_analyzer(captured))
    ids = run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(task_id, runners_module.AnalyzeRequest(limit=10)))

    assert captured == [ids[2]]

    async def cursor_now():
        async with aiosqlite.connect(str(db_path)) as db:
            rows = await db.execute_fetchall(
                "SELECT last_analyzed_ts FROM analysis_state WHERE platform='telegram' AND chat_id='chat-1'"
            )
            return rows[0][0]

    # Cursor advanced even though the run produced zero knowledge items.
    assert run(cursor_now()) == "2026-04-30T12:00:00"


def test_analyze_backlog_beyond_limit_drains_across_runs(tmp_path, monkeypatch):
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
            for idx in range(1, 6):
                ids.append(await _insert_message(
                    db, "chat-1", "Project", f"message-{idx}",
                    f"2026-04-30T1{idx}:00:00", f"tg-{idx}",
                ))
            await db.execute(
                "INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts) VALUES ('telegram','chat-1','2026-04-30T10:00:00')"
            )
            await db.commit()
            return ids

    captured: list[int] = []
    monkeypatch.setattr(analyzer_module, "AnalyzerService", _fake_analyzer(captured))
    ids = run(seed())

    # First run: oldest 2 of the 5-message backlog. Second run: the next 2.
    run(runners_module.run_analyze(create_task("analyze"), runners_module.AnalyzeRequest(limit=2)))
    assert captured == ids[:2]
    run(runners_module.run_analyze(create_task("analyze"), runners_module.AnalyzeRequest(limit=2)))
    assert captured == ids[:4]


def test_first_analyze_seeds_newest_messages(tmp_path, monkeypatch):
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
            ids = [
                await _insert_message(db, "chat-1", "Project", f"message-{idx}",
                                      f"2026-04-30T1{idx}:00:00", f"tg-{idx}")
                for idx in range(1, 4)
            ]
            await db.commit()
            return ids

    captured: list[int] = []
    monkeypatch.setattr(analyzer_module, "AnalyzerService", _fake_analyzer(captured))
    ids = run(seed())

    run(runners_module.run_analyze(create_task("analyze"), runners_module.AnalyzeRequest(limit=2)))
    # No checkpoint yet → newest 2, in chronological order.
    assert captured == ids[1:]


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
            await _insert_message(db, "chat-a", "A", "already analyzed elsewhere", "2026-04-30T12:00:00", "tg-a")
            target = await _insert_message(db, "chat-b", "B", "should be analyzed", "2026-04-30T11:00:00", "tg-b")
            await db.execute(
                "INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts) VALUES ('telegram','chat-a','2026-04-30T12:00:00')"
            )
            await db.commit()
            return target

    captured: list[int] = []
    monkeypatch.setattr(analyzer_module, "AnalyzerService", _fake_analyzer(captured))
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
    import app.services.budget as budget_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(analyzer_module, "load_config", lambda: Config())
    monkeypatch.setattr(
        budget_module,
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


def test_inflight_reservation_blocks_concurrent_overspend(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.core.config import Config
    import app.services.budget as budget_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(
        budget_module,
        "load_config",
        lambda: Config(daily_token_budget=100, budget_action="stop"),
    )
    monkeypatch.setattr(budget_module, "_inflight", 0)
    run(database_module.init_db())

    run(budget_module.reserve(60))
    try:
        run(budget_module.reserve(60))
    except budget_module.TokenBudgetExceeded:
        pass
    else:
        raise AssertionError("second reservation should exceed the budget")
    budget_module.release(60)


def test_save_knowledge_skips_duplicates_and_keeps_todo_category(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.services.knowledge import save_knowledge_items

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    run(database_module.init_db())

    async def seed_and_save():
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id,
                   sender_name, content, msg_type, timestamp, source_id, raw_data)
                   VALUES ('telegram','c1','P','group','u1','A','do it','text','2026-04-30T10:00:00','tg-1','{}')""",
            )
            message_id = cursor.lastrowid
            todo = {
                "title": "Ship parser",
                "content": "Ship the parser",
                "category": "todo",
                "tags": [],
                "source_chat": "P",
                "source_message_ids": json.dumps([message_id]),
                "urgency": 4,
                "batch_id": "b1",
            }
            saved_first = await save_knowledge_items(db, [todo])
            # Same title+chat again → exact-duplicate, skipped.
            saved_second = await save_knowledge_items(db, [dict(todo, batch_id="b2")])
            # A later non-todo item citing the same message must not downgrade it.
            other = {
                "title": "Parser notes",
                "content": "Background on the parser",
                "category": "knowledge",
                "tags": [],
                "source_chat": "P",
                "source_message_ids": json.dumps([message_id]),
                "urgency": 2,
                "batch_id": "b3",
            }
            saved_third = await save_knowledge_items(db, [other])
            await db.commit()
            rows = await db.execute_fetchall(
                "SELECT category, urgency, key_entities FROM analysis_results WHERE message_id=?",
                (message_id,),
            )
            return saved_first, saved_second, saved_third, dict(rows[0])

    saved_first, saved_second, saved_third, analysis = run(seed_and_save())
    assert (saved_first, saved_second, saved_third) == (1, 0, 1)
    assert analysis["category"] == "todo"
    assert analysis["urgency"] == 4
    entities = json.loads(analysis["key_entities"])
    assert len(entities["knowledge_ids"]) == 2


def test_contact_tagging_suggest_confirm_reject_flow(tmp_path, monkeypatch):
    from app.api import tags as tags_api
    from app.core import database as database_module
    from app.core import runners
    from app.core.tasks import create_task
    from app.models.tags import ContactTagAdd, LinkIdsRequest, TagCreate
    from app.services import analyzer as analyzer_module

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)

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


async def seed_messages(db_path, rows):
    async with aiosqlite.connect(str(db_path)) as db:
        for r in rows:
            await db.execute(
                """INSERT INTO messages
                   (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                    content, msg_type, timestamp, source_id, raw_data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                ("wechat", r.get("chat_id", "chat-1"), r.get("chat_name", "家装群"), "group",
                 r.get("sender_id", "u1"), r.get("sender_name", "老张"),
                 r["content"], "text", r["timestamp"], r.get("source_id"), "{}"),
            )
        await db.commit()


def test_ask_answers_from_retrieved_messages(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core import database as database_module
    from app.services.analyzer import AnalyzerService
    from app.services.ask import answer_question

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    run(database_module.init_db())
    run(seed_messages(db_path, [
        {"content": "你家打算什么时候开工？", "timestamp": "2026-08-01T10:00:00", "sender_name": "我"},
        {"content": "装修报价出来了，全包 15 万", "timestamp": "2026-08-01T10:05:00"},
        {"content": "有点贵，再比比价", "timestamp": "2026-08-01T10:06:00", "sender_name": "我"},
        {"content": "周末打球吗", "timestamp": "2026-08-02T09:00:00", "chat_id": "chat-2", "chat_name": "球友群"},
    ]))

    async def fake_llm(self, prompt, purpose=""):
        assert purpose == "ask"
        if prompt.startswith("从用户的问题"):
            return '["装修"]'
        assert "全包 15 万" in prompt  # hit + context made it into the answer prompt
        return "老张报的装修价是全包 15 万【1】"

    monkeypatch.setattr(AnalyzerService, "_call_llm", fake_llm)

    result = run(answer_question("老张说装修报价多少？", []))

    assert result["keywords"] == ["装修"]
    assert "15 万" in result["answer"]
    assert len(result["sources"]) == 1
    src = result["sources"][0]
    assert src["ref"] == 1 and src["chat_name"] == "家装群" and "装修报价" in src["preview"]
    assert result["knowledge"] == []  # no embedding model configured


def test_ask_reports_empty_retrieval_without_llm_answer(tmp_path, monkeypatch):
    from app.core import config as config_module
    from app.core import database as database_module
    from app.services.analyzer import AnalyzerService
    from app.services.ask import answer_question

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    run(database_module.init_db())

    async def fake_llm(self, prompt, purpose=""):
        return '["装修"]'

    monkeypatch.setattr(AnalyzerService, "_call_llm", fake_llm)

    result = run(answer_question("老张说装修报价多少？", []))
    assert result["sources"] == [] and result["knowledge"] == []
    assert "没有检索到" in result["answer"]


def test_generate_digest_persists_report(tmp_path, monkeypatch):
    from datetime import datetime, timedelta

    from app.core import config as config_module
    from app.core import database as database_module
    from app.services.analyzer import AnalyzerService
    from app.services.digest import generate_digest

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    run(database_module.init_db())

    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    old = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    run(seed_messages(db_path, [
        {"content": "装修报价出来了", "timestamp": recent},
        {"content": "收到", "timestamp": recent, "sender_name": "我"},
        {"content": "上个月的旧消息", "timestamp": old},
    ]))

    async def seed_knowledge():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute(
                "INSERT INTO knowledge_items (title, content) VALUES (?,?)",
                ("装修要点", "全包 15 万"),
            )
            await db.commit()

    run(seed_knowledge())

    async def fake_llm(self, prompt, purpose=""):
        assert purpose == "digest"
        assert "消息总量：2 条" in prompt  # old message excluded from the 7d window
        assert "装修要点" in prompt
        return "## 本周概览\n\n测试正文"

    monkeypatch.setattr(AnalyzerService, "_call_llm", fake_llm)

    result = run(generate_digest("weekly"))
    assert result["period"] == "weekly" and result["messages"] == 2

    async def fetch_report():
        async with aiosqlite.connect(str(db_path)) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM reports")
            return rows

    rows = run(fetch_report())
    assert len(rows) == 1
    report = rows[0]
    assert report["id"] == result["id"] and report["period"] == "weekly"
    assert "测试正文" in report["content"]
    assert "数据附录" in report["content"]
    assert "消息总量：2 条" in report["content"]


def test_incremental_analyze_same_second_boundary_not_skipped(tmp_path, monkeypatch):
    """A (timestamp, id) cursor must not skip same-second messages when a LIMIT
    boundary lands mid-second across two runs."""
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
            # Four messages sharing one second, plus a later one.
            for idx in range(1, 5):
                ids.append(await _insert_message(db, "chat-1", "Project", f"m{idx}", "2026-04-30T10:00:00", f"tg-{idx}"))
            ids.append(await _insert_message(db, "chat-1", "Project", "m5", "2026-04-30T10:05:00", "tg-5"))
            # Fresh chat: no checkpoint yet (seed path will pick newest first).
            await db.execute(
                "INSERT INTO analysis_state (platform, chat_id, last_analyzed_ts, last_analyzed_id) "
                "VALUES ('telegram','chat-1','2026-04-30T10:00:00',?)",
                (ids[1],),  # cursor sits at the 2nd same-second message
            )
            await db.commit()
            return ids

    captured: list[int] = []
    monkeypatch.setattr(analyzer_module, "AnalyzerService", _fake_analyzer(captured))
    ids = run(seed())

    task_id = create_task("analyze")
    run(runners_module.run_analyze(task_id, runners_module.AnalyzeRequest(limit=10)))

    # Messages 3 and 4 (same second, id > cursor) plus message 5 must all be seen.
    assert captured == [ids[2], ids[3], ids[4]]


def test_api_security_guard_blocks_rebinding_and_csrf(tmp_path, monkeypatch):
    """Host guard blocks DNS-rebinding on the token-less default; Origin guard
    blocks cross-site state-changing requests; normal localhost use passes."""
    from fastapi.testclient import TestClient
    from app.core import config as config_module
    import app.main as main_module

    monkeypatch.setattr(config_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config_module, "_api_token_cache", "", raising=False)
    client = TestClient(main_module.app)

    assert client.get("/api/tasks", headers={"host": "127.0.0.1:8000"}).status_code == 200
    assert client.get("/api/tasks", headers={"host": "attacker.example:8000"}).status_code == 403
    csrf = client.post("/api/sync/all", headers={"host": "127.0.0.1:8000", "origin": "http://evil.example"})
    assert csrf.status_code == 403
    # Same-origin POST clears the guard (downstream status may vary, just not 403).
    ok = client.post("/api/sync/all", headers={"host": "127.0.0.1:8000", "origin": "http://127.0.0.1:8000"})
    assert ok.status_code != 403


def test_qq_launcher_logs_redact_tokens():
    from app.services.sync.qq_launcher import _redact_tokens

    raw = "[QCE] Token: abcdef0123456789ABCDEF0123\naccess_token=SECRETVALUE123456\nkeep this line"
    out = _redact_tokens(raw)
    assert "abcdef0123456789ABCDEF0123" not in out
    assert "SECRETVALUE123456" not in out
    assert "********" in out and "keep this line" in out
