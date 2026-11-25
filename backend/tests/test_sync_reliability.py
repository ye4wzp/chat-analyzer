import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import aiosqlite
import pytest


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core import database, config
    from app.services.sync import wechat, telegram_live
    path = tmp_path / "chat.db"
    monkeypatch.setattr(database, "DB_PATH", path)
    monkeypatch.setattr(wechat, "DB_PATH", path)
    monkeypatch.setattr(telegram_live, "DB_PATH", path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    run(database.init_db())
    return path


def test_wechat_all_sessions_and_stable_ids(store, monkeypatch):
    from app.services.sync import wechat
    calls = []
    async def fake(*args):
        calls.append(args)
        if args[0] == "sessions":
            n = int(args[2])
            return json.dumps({"sessions": [{"username": str(i), "name": "same name"} for i in range(min(n, 1001))]})
        return '{"messages": []}'
    monkeypatch.setattr(wechat, "_run_wx", fake)
    result = run(wechat.sync_sessions())
    assert result["chats"] == result["targets"] == 1001
    assert [c[2] for c in calls if c[0] == "sessions"] == ["1000", "2000"]
    assert all(c[1] != "same name" for c in calls if c[0] == "history")


def test_wechat_failure_does_not_advance_watermark(store, monkeypatch):
    from app.services.sync import wechat
    async def fake(*args):
        if args[0] == "sessions":
            return '{"sessions":[{"username":"c","name":"name"}]}'
        if "--offset" in args and args[args.index("--offset") + 1] == "0":
            return json.dumps({"messages": [{"local_id": i + 1, "timestamp": 1760000000, "content": "hello"} for i in range(500)]})
        raise RuntimeError("offline")
    monkeypatch.setattr(wechat, "_run_wx", fake)
    r = run(wechat.sync_sessions())
    assert r["details"][0]["status"] == "failed"
    async def check():
        async with aiosqlite.connect(store) as db:
            assert (await db.execute_fetchall('SELECT count(*) FROM messages'))[0][0] == 0
            assert not await db.execute_fetchall('SELECT * FROM sync_state')
    run(check())


def test_wechat_full_ignores_since_and_new_failure_surfaces(store, monkeypatch):
    from app.services.sync import wechat
    calls = []
    async def work():
        async with aiosqlite.connect(store) as db:
            await db.execute("INSERT INTO sync_state(platform,chat_id,last_timestamp) VALUES('wechat','c','2026-10-25T00:00:00')")
            await db.commit()
            async def fake(*args):
                calls.append(args)
                return '{"messages":[]}'
            monkeypatch.setattr(wechat, "_run_wx", fake)
            await wechat._sync_chat_history(db, 'c', 'same', full=True)
            assert '--since' not in calls[0]
            assert (await db.execute_fetchall('SELECT last_timestamp FROM sync_state'))[0][0] == '2026-10-25T00:00:00'
    run(work())
    async def fail(*args):
        raise RuntimeError('offline')
    monkeypatch.setattr(wechat, '_run_wx', fail)
    with pytest.raises(RuntimeError):
        run(wechat.sync_new_messages())


def test_telegram_commits_contiguous_prefix_then_resumes(store, monkeypatch):
    from app.services.sync import telegram_live as tg
    from app.services.sync.result import result_status
    class Fake:
        fail = True
        starts = []
        async def iter_dialogs(self):
            yield SimpleNamespace(id=123, entity=SimpleNamespace())
        async def iter_messages(self, entity, *, min_id, reverse):
            assert reverse is True
            self.starts.append(min_id)
            for i in range(min_id + 1, 252):
                if self.fail and i == 251:
                    raise ConnectionError('offline')
                yield SimpleNamespace(id=i, action=None, message=f'm{i}', media=None, sender=None,
                                      date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    client = Fake()
    async def ensure():return client
    monkeypatch.setattr(tg, '_ensure_client', ensure)
    monkeypatch.setattr(tg, '_chat_kind', lambda e: ('private', 'c'))
    first = run(tg.sync_all())
    assert first['imported'] == 200 and result_status(first) == 'partial'
    client.fail = False
    second = run(tg.sync_all())
    assert client.starts == [0, 200]
    assert second['imported'] == 51 and result_status(second) == 'done'
    async def check():
        async with aiosqlite.connect(store) as db:
            assert (await db.execute_fetchall('SELECT count(*) FROM messages'))[0][0] == 251
            assert (await db.execute_fetchall('SELECT last_msg_id FROM sync_state'))[0][0] == '251'
    run(check())


def test_qq_snapshot_pagination_and_local_credentials(monkeypatch):
    from app.core.config import QQConfig
    from app.services.sync import qq_qce as qq, qq_launcher
    monkeypatch.setattr(qq_launcher, '_token_from_disk', lambda: 'active')
    async def check():
        q = qq.QCEClient(QQConfig(token='stale'))
        assert q.cfg.token == 'active'
        bodies = []
        async def post(path, body):
            bodies.append(body)
            return {'hasNext': False, 'totalCount': 601, 'messages': []}
        q._post = post
        assert (await q.fetch_messages(2, 'c', page=1))['hasNext'] is True
        assert (await q.fetch_messages(2, 'c', page=4))['hasNext'] is False
        assert bodies[0]['filter'] == bodies[1]['filter']
        await q._client.aclose()
        remote = qq.QCEClient(QQConfig(host='remote.example', token='remote'))
        assert remote.cfg.token == 'remote'
        await remote._client.aclose()
    run(check())


def test_sync_task_journal_survives_restart_as_interrupted(store):
    from app.core import tasks
    tid = tasks.create_task('sync_wechat')
    tasks._tasks[tid].update(status='running', request={'platforms':['wechat']}, results={'wechat':{'imported':3}})
    tasks._tasks.clear()
    tasks.restore_tasks()
    assert tasks._tasks[tid]['status'] == 'interrupted'
    assert tasks._tasks[tid]['results']['wechat']['imported'] == 3
    assert not tasks.is_task_running('sync_wechat')
    tasks._tasks[tid]['_finished_at'] = 1
    assert any(t['id'] == tid for t in tasks.list_tasks())


def test_partial_result_is_not_success_and_retry_is_targeted(store, monkeypatch):
    from app.core import tasks, runners
    from app.api import tasks as api
    tid = tasks.create_task('sync_wechat')
    tasks._tasks[tid].update(request={'platforms':['wechat'],'mode':'repair'}, results={'wechat':{
        'chats':1, 'imported':4,'details':[{'chat_id':'ok','status':'done'}, {'chat_id':'bad','status':'failed'}]}})
    runners.finish_sync_task(tid)
    assert tasks._tasks[tid]['status'] == 'partial'
    captured=[]
    async def fake(*args, **kwargs):captured.append(kwargs)
    monkeypatch.setattr(runners,'run_platforms_sync',fake)
    # Execute within the existing loop rather than nesting asyncio.run.
    def spawn(tid,coro):return asyncio.create_task(coro)
    monkeypatch.setattr(tasks,'spawn_cancellable',spawn)
    async def check():
        await api.retry_sync(tid)
        await asyncio.sleep(0)
    run(check())
    assert captured[0]['selections']=={'wechat':['bad']}


def test_retry_disappeared_wechat_target_is_not_done(store, monkeypatch):
    from app.services.sync import wechat
    from app.services.sync.result import result_status
    async def fake(*args):return '{"sessions":[]}'
    monkeypatch.setattr(wechat,'_run_wx',fake)
    r=run(wechat.sync_sessions(chat_ids=['missing']))
    assert r['targets']==1 and result_status(r)=='error'
    assert r['details'][0]['chat_id']=='missing'


@pytest.mark.parametrize('fresh_done',[True,False])
def test_qq_old_snapshot_finishes_with_new_window(store, monkeypatch, fresh_done):
    from app.services.sync import qq_qce as qq
    q=SimpleNamespace(cutoff=None)
    cuts=[]
    async def walk(*args,**kwargs):
        cuts.append(q.cutoff)
        w=qq._Walk();w.done=True;w.last_page=1
        if len(cuts)==3:
            w.max_seq=102;w.inserted=2;w.done=fresh_done
        return w
    monkeypatch.setattr(qq,'_walk_pages',walk)
    monkeypatch.setattr(qq.time,'time',lambda:2.0)
    async def check():
        async with aiosqlite.connect(store) as db:
            await db.execute("INSERT INTO sync_state(platform,chat_id,last_msg_id,backfill_page,backfill_cutoff) VALUES('qq','g','100',3,1000)")
            got,pending=await qq.sync_one_chat(db,q,kind='group',peer={'groupCode':'g'})
            assert got==2 and pending is (not fresh_done)
            row=(await db.execute_fetchall("SELECT last_msg_id,backfill_cutoff FROM sync_state WHERE chat_id='g'"))[0]
            assert row[0]=='102' and row[1]==(None if fresh_done else 2000)
    run(check())
    assert cuts==[1000,1000,2000]


def test_wechat_cache_does_not_hide_live_failure(store, tmp_path, monkeypatch):
    import hashlib, sqlite3
    from app.core.config import Config
    from app.services.sync import wechat_cache as cache
    from app.services.sync.result import result_status
    home=tmp_path/'wx';root=home/'cache';root.mkdir(parents=True)
    source=root/'cached.db';table='Msg_'+hashlib.md5(b'c').hexdigest()
    with sqlite3.connect(source) as src:
        src.execute('CREATE TABLE Name2Id(user_name TEXT)');src.execute("INSERT INTO Name2Id VALUES('c')")
        src.execute(f'CREATE TABLE "{table}"(local_id INTEGER,local_type INTEGER,create_time INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,real_sender_id INTEGER)')
        src.execute(f'INSERT INTO "{table}" VALUES(1,1,1760000000,\'cached\',0,1)')
    (root/'_mtimes.json').write_text(json.dumps({'message/message_0.db':{'path':str(source)}}))
    monkeypatch.setattr(cache,'WX_HOME',home)
    r={'platform':'wechat','targets':1,'chats':0,'imported':0,'excluded':0,
       'details':[{'chat_id':'c','name':'C','status':'failed','error':'Offline','imported':0}]}
    run(cache.repair_cache(r,Config()))
    assert r['imported']==1 and result_status(r)=='partial'
    assert r['details'][0]['status']=='pending'
    run(cache.repair_cache(r,Config()))
    assert r['imported']==1
    with sqlite3.connect(store) as db:
        assert not db.execute("SELECT last_timestamp FROM sync_state WHERE platform='wechat' AND chat_id='c'").fetchall()


def test_unexpected_task_failure_releases_lock_and_keeps_results(store):
    from app.core import tasks
    async def check():
        tid = tasks.create_task("sync_wechat")
        tasks._tasks[tid].update(status="running", results={"wechat": {"imported": 10}})
        async def broken(): raise RuntimeError("synthetic")
        await tasks.spawn_cancellable(tid, broken())
        assert tasks._tasks[tid]["status"] == "error"
        assert tasks._tasks[tid]["resume_all"] is True
        assert tasks._tasks[tid]["results"]["wechat"]["imported"] == 10
        assert not tasks.is_task_running("sync_wechat")
    run(check())


def test_bad_task_journal_does_not_block_startup(store):
    from app.core import tasks
    journal = store.with_name("sync-tasks.db")
    journal.write_bytes(b"not a database")
    tasks.restore_tasks()
    journal.unlink()
    with tasks._store() as db:
        db.execute("INSERT INTO tasks VALUES('bad','null',1)")
        db.execute("INSERT INTO tasks VALUES('ok',?,2)", (json.dumps(dict(type="sync_qq",status="done")),))
    db.close()
    tasks.restore_tasks()
    assert "ok" in tasks._tasks and "bad" not in tasks._tasks


def test_wechat_invalid_response_is_not_success(store, monkeypatch):
    from app.services.sync import wechat
    with pytest.raises(ValueError): wechat._rows('{"error":"unavailable"}', "sessions")
    async def fake(*args):
        if args[0] == "sessions": return '{"sessions":[{"username":"c"}]}'
        return '{"messages":[{"local_id":1,"content":"missing timestamp"}]}'
    monkeypatch.setattr(wechat, "_run_wx", fake)
    result = run(wechat.sync_sessions())
    assert result["details"][0]["status"] == "failed"


def test_unexpected_failure_retries_original_scope_and_health_stays_error(store, monkeypatch):
    from app.core import tasks, runners
    from app.api import tasks as api, sync
    tid = tasks.create_task('sync_qq')
    tasks._tasks[tid].update(status='error', resume_all=True,
        request={'platforms':['qq'], 'selections':{'qq':['a','b']}},
        results={'qq':{'chats':1,'imported':1,'details':[{'chat_id':'a','status':'done'}]}})
    assert run(sync.sync_health())['runs'][0]['status'] == 'error'
    captured=[]
    async def fake(*args, **kwargs): captured.append(kwargs)
    monkeypatch.setattr(runners, 'run_platforms_sync', fake)
    monkeypatch.setattr(tasks, 'spawn_cancellable', lambda tid, coro: asyncio.create_task(coro))
    async def check():
        await api.retry_sync(tid)
        await asyncio.sleep(0)
    run(check())
    assert captured[0]['selections'] == {'qq':['b']}


def test_legacy_resume_and_second_interruption():
    from app.services.sync.result import resume_scope, merge_attempt
    req = {"platforms": ["wechat", "telegram", "qq"]}
    done = {"chat_id": "a", "status": "done", "imported": 2}
    failed = {"chat_id": "b", "status": "failed", "imported": 0}
    folded = {"chat_id": "@placeholder_foldgroup", "status": "failed"}
    assert resume_scope("wechat", {"targets": 3, "details": [done, failed, folded]}, req, True)[:3] == (True, ["b"], {"a"})
    partial = {"targets": 3, "details": [done], "imported": 2}
    assert resume_scope("telegram", partial, req, True)[:3] == (True, None, {"a"})
    current = {"platform": "telegram", "target_ids": ["b", "c"], "details": [{**done, "chat_id":"b"}], "imported":2}
    merged = merge_attempt(partial, current)
    assert merged["imported"] == 4 and merged["chats"] == 2
    assert resume_scope("telegram", merged, req, True)[:3] == (True, ["c"], {"a","b"})


def test_folded_wechat_and_safe_error(store, monkeypatch):
    from app.services.sync import wechat
    from app.services.sync.result import error_reason
    import httpx
    calls = []
    async def fake(*args):
        if args[0] == "sessions":
            return json.dumps({"sessions": [{"username": cid} for cid in ["a", "@placeholder_foldgroup", "@real"]]})
        calls.append(args[1])
        raise RuntimeError("服务不可用 token=secret")
    monkeypatch.setattr(wechat, "_run_wx", fake)
    r = run(wechat.sync_sessions(chat_ids={"a", "@placeholder_foldgroup", "@real"}, skip_ids={"a"}))
    assert calls == ["@real"] and len(r["details"]) == 1
    assert r["details"][0]["error"] == "服务不可用 token=***"
    req = httpx.Request("GET", "http://localhost/api/groups?token=secret")
    exc = httpx.HTTPStatusError("denied", request=req, response=httpx.Response(403, request=req))
    assert error_reason(exc) == "HTTP 403 Forbidden（/api/groups）"


def test_repair_resume_skips_completed_live_reads(store, monkeypatch):
    from app.services.sync import wechat, wechat_cache
    from app.services.sync.result import resume_scope
    prev = {"details": [{"chat_id":"a","status":"done","cache_checked":True},
                        {"chat_id":"b","status":"done"}]}
    go, selected, skip, live = resume_scope("wechat", prev, {"mode":"repair"}, True)
    assert go and selected is None and skip == {"a"} and live == {"b"}
    calls = []
    async def fake(*args):
        if args[0] == "sessions":
            return json.dumps({"sessions": [{"username": x} for x in ["a","b","c"]]})
        calls.append(args[1])
        return '{"messages":[]}'
    async def cache(result, cfg, **kwargs):
        assert kwargs["skip_ids"] == {"a"}
        assert [d["chat_id"] for d in result["details"]] == ["b","c"]
    monkeypatch.setattr(wechat, "_run_wx", fake)
    monkeypatch.setattr(wechat_cache, "repair_cache", cache)
    run(wechat.sync_sessions(mode="repair", skip_ids=skip, live_only=live))
    assert calls == ["c"]


def test_saved_targets_survive_platform_error():
    from app.services.sync.result import resume_scope, result_status
    checkpoint = {"target_ids": ["a", "b"], "details": [
        {"chat_id": "a", "status": "done"}, {"chat_id": "@platform", "status": "failed"}]}
    assert resume_scope("qq", checkpoint, {}, True)[:2] == (True, ["b"])
    assert result_status({"target_ids":["a","b"], "details":[checkpoint["details"][0]]}) == "partial"


def test_short_legacy_directory_stays_pending():
    from app.services.sync.result import merge_attempt, resume_scope, result_status
    prev = {"platform":"telegram", "targets":3, "details":[{"chat_id":"a","status":"done"}]}
    fresh = {"platform":"telegram", "target_ids":["b"], "details":[{"chat_id":"b","status":"done"}]}
    snapshot = merge_attempt(prev, fresh)
    assert result_status(snapshot) == "partial" and snapshot["targets"] == 3
    assert resume_scope("telegram", snapshot, {}, False)[:3] == (True, None, {"a", "b"})


def test_missing_cached_table_is_not_success(store, tmp_path, monkeypatch):
    import sqlite3
    from app.core.config import Config
    from app.services.sync import wechat_cache as cache
    src = tmp_path/'empty.db'
    with sqlite3.connect(src) as db:
        db.execute("CREATE TABLE Name2Id(user_name TEXT)")
    monkeypatch.setattr(cache, "cache_sources", lambda: [src])
    result = {"platform":"wechat", "targets":1, "chats":1, "imported":0,
              "details":[{"chat_id":"a", "name":"A", "status":"done", "imported":0}]}
    run(cache.repair_cache(result, Config(), expected_cache={"a"}))
    assert result["details"][0]["status"] == "pending"
    assert result["details"][0]["live_done"]


def test_batch_sync_exclusions_merge_by_platform_and_preserve_analysis(store):
    from app.api.config import update_config
    from app.models.config import ConfigUpdate
    from app.core.config import load_config
    from app.services.sync._common import in_sync_scope

    existing = {"platform": "wechat", "chat_id": "keep"}
    a = {"platform": "wechat", "chat_id": "123"}
    b = {"platform": "qq", "chat_id": "123"}
    run(update_config(ConfigUpdate(add_sync_exclude=existing, add_chat_ref=existing)))
    change = ConfigUpdate(add_sync_excludes=[a, b, a, existing])
    for _ in range(2):
        run(update_config(change))
    cfg = load_config()
    assert [r.model_dump() for r in cfg.sync_scope.exclude_chats] == [existing, a, b]
    assert [r.model_dump() for r in cfg.chat_filter.refs] == [existing]
    for ref in (existing, a, b):
        assert not in_sync_scope(ref["platform"], ref["chat_id"], "group", cfg)
    assert in_sync_scope("telegram", "123", "group", cfg)
