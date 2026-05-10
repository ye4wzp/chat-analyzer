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
