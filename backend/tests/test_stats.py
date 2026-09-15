"""Pre-aggregated stats: classification, incremental refresh, and the read API."""
import asyncio

import aiosqlite


def run(coro):
    return asyncio.run(coro)


def test_classify_kind_prefers_content_markers():
    from app.services.stats import classify_kind

    assert classify_kind("wechat", "文本", "哈哈") == "text"
    assert classify_kind("wechat", "链接/文件", "[引用] 原来codex用不完的\n  ↳ x") == "quote"
    assert classify_kind("wechat", "链接/文件", "[小程序] 拼多多") == "miniprogram"
    assert classify_kind("wechat", "链接/文件", "[链接] <![CDATA[微信红包]]>") == "redpacket"
    assert classify_kind("wechat", "链接/文件", "[链接] <![CDATA[微信转账]]>") == "transfer"
    assert classify_kind("wechat", "链接/文件", "[链接] 当代年轻人") == "link"
    assert classify_kind("wechat", "系统", '[系统] "player" 撤回了一条消息') == "system"
    assert classify_kind("qq", "text", "[图片]\n") == "image"
    assert classify_kind("qq", "text", "[引用回复] xx") == "quote"
    assert classify_kind("qq", "text", "[表情包]") == "sticker"
    assert classify_kind("telegram", "media", "[媒体]") == "media"
    assert classify_kind("telegram", "text", "今天聊聊微信红包的运营玩法") == "text"
    assert classify_kind("telegram", "sticker", "") == "sticker"
    assert classify_kind("wechat", "type=67", "") == "other"
    assert classify_kind("wechat", "链接/文件", '"Emma" 拍了拍 "申米"') == "system"
    assert classify_kind("wechat", "链接/文件", "当前微信版本不支持展示该内容，请升级至最新版本。") == "system"


def test_extract_resources_links_files_payments():
    from app.services.stats import extract_resources

    links = extract_resources("text", "看这个 https://example.com/a?x=1 和 http://b.cn/p。")
    assert [(k, u) for k, u, _ in links] == [("link", "https://example.com/a?x=1"), ("link", "http://b.cn/p")]
    assert links[0][2] == "看这个 和 。"

    assert extract_resources("link", "[链接] 年度报告.pdf") == [("file", None, "年度报告.pdf")]
    assert extract_resources("link", "[链接] 某篇公众号文章标题") == [("link", None, "某篇公众号文章标题")]
    assert extract_resources("redpacket", "[链接] <![CDATA[微信红包]]>") == [("redpacket", None, "微信红包")]
    assert extract_resources("miniprogram", "[小程序] 美团外卖") == [("miniprogram", None, "美团外卖")]
    assert extract_resources("text", "纯文本") == []


def test_longest_streak():
    from app.api.stats import _longest_streak

    assert _longest_streak([]) == {"days": 0, "start": None, "end": None}
    streak = _longest_streak(["2026-01-01", "2026-01-02", "2026-01-04", "2026-01-05", "2026-01-06", "2026-02-01"])
    assert streak == {"days": 3, "start": "2026-01-04", "end": "2026-01-06"}


async def _seed(db_path, rows):
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executemany(
            """INSERT INTO messages (platform, chat_id, chat_name, chat_type, sender_id, sender_name,
                                     content, msg_type, timestamp, source_id)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        await db.commit()


def test_stats_refresh_is_incremental_and_api_reads_rollups(tmp_path, monkeypatch):
    from app.core import database as database_module
    from app.services import stats as stats_module
    from app.api import stats as stats_api

    db_path = tmp_path / "chat.db"
    monkeypatch.setattr(database_module, "DB_PATH", db_path)
    run(database_module.init_db())

    run(_seed(db_path, [
        ("wechat", "g1@chatroom", "群A", "group", "", "小明", "早上好", "文本", "2026-01-05T09:10:00", "1"),
        ("wechat", "g1@chatroom", "群A", "group", "", "小红", "[图片] l", "图片", "2026-01-05T21:00:00", "2"),
        ("wechat", "g1@chatroom", "群A", "group", "", "小明", "看 https://x.io/p", "文本", "2026-02-01T09:30:00", "3"),
        ("wechat", "u1", "老王", "private", "", "老王", "[链接] <![CDATA[微信红包]]>", "链接/文件", "2026-02-02T12:00:00", "4"),
        ("telegram", "t1", "TG群", "group", "101", "Bob", "[媒体]", "media", "2026-02-03T23:05:00", "5"),
    ]))

    first = run(stats_module.refresh())
    assert first["processed"] == 5
    status = run(stats_module.get_status())
    assert status["ready"] and status["behind"] == 0

    overview = run(stats_api.stats_overview())
    assert overview["totals"] == {
        "messages": 5, "chats": 3, "groups": 2, "privates": 1, "active_months": 2,
        "first_ts": "2026-01-05T09:10:00", "last_ts": "2026-02-03T23:05:00",
    }
    assert [m["count"] for m in overview["monthly"]] == [2, 3]
    assert overview["hourly"][9] == 2 and overview["hourly"][21] == 1 and overview["hourly"][23] == 1
    kinds = {k["kind"]: k["count"] for k in overview["kinds"]}
    assert kinds == {"text": 2, "image": 1, "redpacket": 1, "media": 1}
    assert overview["top_chats"][0]["chat_name"] == "群A" and overview["top_chats"][0]["count"] == 3

    # Filtered by platform / chat_type / channel exclusion.
    groups_only = run(stats_api.stats_overview(platform="wechat", chat_type="group"))
    assert groups_only["totals"]["messages"] == 3 and groups_only["totals"]["chats"] == 1
    run(_seed(db_path, [
        ("telegram", "ch1", "某频道", "channel", "", "", "推送", "text", "2026-02-04T10:00:00", "c1"),
    ]))
    run(stats_module.refresh())
    assert run(stats_api.stats_overview())["totals"]["messages"] == 6
    no_ch = run(stats_api.stats_overview(exclude_channel=True))
    assert no_ch["totals"]["messages"] == 5 and no_ch["hourly"][10] == 0
    assert all(c["chat_type"] != "channel" for c in no_ch["top_chats"])

    async def _drop_channel():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("DELETE FROM messages WHERE source_id='c1'")
            await db.commit()
    run(_drop_channel())
    run(stats_module.refresh(full=True))  # back to the 5-message baseline

    # Incremental: a new message only touches the new rows.
    run(_seed(db_path, [
        ("wechat", "g1@chatroom", "群A", "group", "", "小明", "又来了", "文本", "2026-02-10T09:00:00", "6"),
    ]))
    second = run(stats_module.refresh())
    assert second["processed"] == 1
    members = run(stats_api.stats_members("wechat", "g1@chatroom"))
    assert members["total_messages"] == 4 and members["member_count"] == 2
    top = members["members"][0]
    assert top["name"] == "小明" and top["count"] == 3 and top["hours"][9] == 3

    cal = run(stats_api.stats_calendar(year=2026, platform="wechat"))
    assert cal["active_days"] == 4 and cal["peak"]["day"] == "2026-01-05"

    res = run(stats_api.list_resources())
    kinds_found = sorted(r["kind"] for r in res["items"])
    assert kinds_found == ["link", "redpacket"]
    assert res["counts"] == {"link": 1, "redpacket": 1}

    chat = run(stats_api.stats_chat("wechat", "g1@chatroom"))
    assert chat["messages"] == 4 and chat["distinct_senders"] == 2

    yearly = run(stats_api.stats_yearly(year=2026))
    assert yearly["totals"]["messages"] == 6 and yearly["totals"]["active_days"] == 5
    assert yearly["peak_day"]["day"] == "2026-01-05"
    assert [m["count"] for m in yearly["monthly"][:2]] == [2, 4]
    assert yearly["top_groups"][0]["chat_name"] == "群A"
    assert yearly["top_privates"][0]["chat_name"] == "老王"
    assert yearly["resources"] == {"link": 1, "redpacket": 1}

    # 多号: relabel the unlabeled wechat rows as 主号 and add one message from a
    # second account; stats split by account and the switch lists both.
    from app.core import tasks as tasks_module
    monkeypatch.setattr(stats_module, "spawn_refresh_if_needed", lambda **kw: None)
    assigned = run(stats_api.assign_account(stats_api.AccountAssign(platform="wechat", from_account="", to_account="主号")))
    assert assigned["updated"] == 5
    run(_seed(db_path, [
        ("wechat", "g9@chatroom", "小号的群", "group", "", "小号", "hi", "文本", "2026-03-01T08:00:00", "7"),
    ]))
    async def _label_last():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("UPDATE messages SET account='小号' WHERE source_id='7'")
            await db.commit()
    run(_label_last())
    run(stats_module.refresh(full=True))
    accounts = run(stats_api.stats_accounts())
    assert {(a["platform"], a["account"], a["messages"]) for a in accounts} == {
        ("wechat", "主号", 5), ("wechat", "小号", 1), ("telegram", "", 1),
    }
    assert run(stats_api.stats_overview(platform="wechat", account="小号"))["totals"]["messages"] == 1
    assert run(stats_api.stats_overview(platform="wechat", account="主号"))["totals"]["messages"] == 5
    assert not tasks_module.is_task_running("stats")

    # Deleting messages (or restoring an older backup) leaves the cursor ahead
    # of MAX(id): status must flag it and the next refresh must rebuild.
    async def _delete_last():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("DELETE FROM messages WHERE source_id='7'")
            await db.commit()
    run(_delete_last())
    status = run(stats_module.get_status())
    assert status["stale"] and not status["ready"]
    rebuilt = run(stats_module.refresh())
    assert rebuilt["processed"] == 6
    assert run(stats_module.get_status())["ready"]
    assert run(stats_api.stats_overview())["totals"]["messages"] == 6
    run(_seed(db_path, [
        ("wechat", "g9@chatroom", "小号的群", "group", "", "小号", "hi", "文本", "2026-03-01T08:00:00", "7"),
    ]))
    run(_label_last())
    run(stats_module.refresh())

    # A version bump forces a full rebuild with identical results.
    monkeypatch.setattr(stats_module, "STATS_VERSION", "test-bump")
    rebuilt = run(stats_module.refresh())
    assert rebuilt["processed"] == 7
    assert run(stats_api.stats_overview())["totals"]["messages"] == 7
