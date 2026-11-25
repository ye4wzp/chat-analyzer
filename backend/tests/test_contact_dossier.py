"""Synthetic contact records and fake model only; no real accounts or LLM."""
import asyncio
import json
import sqlite3
import pytest
from fastapi import HTTPException
from app.api.contact_dossier import Decision, NoteUpdate
from app.services import contact_dossier as svc


def run(coro): return asyncio.run(coro)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.core import database, config
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis, 'EXTRACT_CONCURRENCY', 1)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'chat.db')
    monkeypatch.setattr(config, 'CONFIG_PATH', tmp_path / 'config.json')
    run(database.init_db())
    with sqlite3.connect(database.DB_PATH) as db:
        for i in range(1, 13):
            db.execute('INSERT INTO messages(id,platform,chat_id,chat_name,chat_type,sender_id,sender_name,content,timestamp) VALUES(?,?,?,?,?,?,?,?,?)',
                (i,'qq','c','同名联系人','private','target' if i <= 10 else 'me','对方' if i <= 10 else '我',f'开发网站 {i}','2026-10-26T12:00:00'))
        for i, platform, cid, kind in [(13,'telegram','c','private'),(14,'qq','g','group')]:
            db.execute('INSERT INTO messages(id,platform,chat_id,chat_name,chat_type,sender_id,content,timestamp) VALUES(?,?,?,?,?,?,?,?)',
                (i,platform,cid,'同名联系人',kind,'target','其他来源','2026-10-26T12:00:00'))
        db.execute("INSERT INTO persons(name,notes) VALUES('同名联系人','原来的人物备注')")
    return database.DB_PATH


def suggestions():
    return [dict(kind='tag',value='网站开发',basis='self_report',reason='对方自述',source_ids=[1,2]),
        dict(kind='short_note',value='同名联系人｜网站开发',basis='self_report',reason='业务自述',source_ids=[1]),
        dict(kind='detail_note',value='对方表示会开发网站，合作意愿待核实。',basis='interaction',reason='往来记录',source_ids=[2,3])]


def fake_model(monkeypatch, items=None):
    calls = []
    async def call(self, prompt, **kwargs):
        calls.append(prompt)
        if kwargs['purpose'] == 'contact_dossier_extract':
            messages = json.loads(prompt.split('聊天记录：\n')[-1])
            ids = sorted({m['id'] for m in messages if m['role'] == '对方' and m['content'].strip()})
            return json.dumps([dict(fact='业务自述',basis='self_report',source_ids=ids[i:i+8]) for i in range(0,len(ids),8)])
        assert kwargs['purpose'] == 'contact_dossier_final'
        return json.dumps(suggestions() if items is None else items, ensure_ascii=False)
    monkeypatch.setattr(svc.AnalyzerService, '_call_llm', call)
    return calls


def test_draft_persists_without_effect_until_review(store, monkeypatch):
    calls = fake_model(monkeypatch)
    result = run(svc.generate('qq','c','target'))
    assert result['current']['short_note'] == '' and result['current']['tags'] == []
    assert len(result['draft']['items']) == 3
    assert result['draft']['coverage']['processed'] == 12 and result['draft']['coverage']['target_processed'] == 10
    assert '其他来源' not in calls[0] and '"role":"其他/我"' in calls[0]
    assert run(svc.load_dossier('qq','c'))['draft']['id'] == result['draft']['id']
    with sqlite3.connect(store) as db:
        assert db.execute('SELECT count(*) FROM contact_tag_links').fetchone()[0] == 0
        assert db.execute('SELECT notes FROM persons').fetchone()[0] == '原来的人物备注'


def test_selective_edited_acceptance_keeps_evidence(store, monkeypatch):
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    result = run(svc.review('qq','c',draft['id'],[Decision(index=0,action='accept'),Decision(index=1,action='accept',value='网站开发联系人'),Decision(index=2,action='reject')]))
    c = result['current']
    assert c['short_note'] == '网站开发联系人' and c['short_locked']
    assert c['detail_note'] == '' and len(c['tags']) == 1
    assert c['evidence']['short_note']['source_ids'] == [1] and c['evidence']['short_note']['edited'] is True
    assert result['draft'] is None
    assert run(svc.load_dossier('telegram','c'))['current']['short_note'] == ''
    with pytest.raises(HTTPException) as exc: run(svc.review('qq','c',draft['id'],[]))
    assert exc.value.status_code == 409


@pytest.mark.parametrize('bad_id', [11,13,14,999,True,'1'])
def test_foreign_self_fabricated_evidence_rejected(store, monkeypatch, bad_id):
    fake_model(monkeypatch,[dict(suggestions()[0],source_ids=[bad_id])])
    with pytest.raises(HTTPException) as exc: run(svc.generate('qq','c','target'))
    assert exc.value.status_code == 422 and run(svc.load_dossier('qq','c'))['draft'] is None


def test_wrong_chat_and_insufficient_sender_do_not_call_model(store, monkeypatch):
    calls = fake_model(monkeypatch)
    for cid,sender in [('g','target'),('c','missing'),('c','me')]:
        with pytest.raises(HTTPException): run(svc.generate('qq',cid,sender))
    assert not calls


def test_manual_locks_and_stale_draft_protect_notes(store, monkeypatch):
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    saved = run(svc.save_manual('qq','c',NoteUpdate(version=0,short_note='手写备注',detail_note='重要信息')))
    assert saved['current']['short_locked'] and saved['current']['detail_locked']
    with pytest.raises(HTTPException) as exc:
        run(svc.review('qq','c',draft['id'],[Decision(index=i,action='accept') for i in range(3)]))
    assert exc.value.status_code == 409
    result = run(svc.generate('qq','c','target'))
    assert [i['kind'] for i in result['draft']['items']] == ['tag'] and result['current']['short_note'] == '手写备注'
    with pytest.raises(HTTPException): run(svc.save_manual('qq','c',NoteUpdate(version=0,short_note='旧页面')))


def test_rejection_suppressed_until_new_evidence(store, monkeypatch):
    fake_model(monkeypatch,[suggestions()[0]])
    draft = run(svc.generate('qq','c','target'))['draft']
    run(svc.review('qq','c',draft['id'],[Decision(index=0,action='reject')]))
    next_draft = run(svc.generate('qq','c','target'))['draft']
    assert next_draft['items'] == [] and next_draft['coverage']['suppressed'] == 1
    fake_model(monkeypatch,[dict(suggestions()[0],source_ids=[1,3])])
    assert run(svc.generate('qq','c','target'))['draft']['items'] == []
    with sqlite3.connect(store) as db:
        db.execute("INSERT INTO messages(id,platform,chat_id,chat_type,sender_id,content,timestamp) VALUES(15,'qq','c','private','target','新的自述','2026-10-27T12:00:00')")
    fake_model(monkeypatch,[dict(suggestions()[0],source_ids=[1,15])])
    assert len(run(svc.generate('qq','c','target'))['draft']['items']) == 1


def test_missing_evidence_rolls_back_whole_review(store, monkeypatch):
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    with sqlite3.connect(store) as db: db.execute('DELETE FROM messages WHERE id=3')
    with pytest.raises(HTTPException):
        run(svc.review('qq','c',draft['id'],[Decision(index=0,action='accept'),Decision(index=1,action='reject'),Decision(index=2,action='accept')]))
    result = run(svc.load_dossier('qq','c'))
    assert result['current']['tags'] == [] and result['draft'] is not None
    with sqlite3.connect(store) as db: assert db.execute('SELECT count(*) FROM contact_dossier_rejections').fetchone()[0] == 0


def test_explicit_decision_for_every_item(store, monkeypatch):
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    for choices in [[],[Decision(index=0,action='accept')]*3]:
        with pytest.raises(HTTPException) as exc: run(svc.review('qq','c',draft['id'],choices))
        assert exc.value.status_code == 400


def test_backup_restores_notes_locks_drafts_feedback(store, monkeypatch, tmp_path):
    from app.core import database
    from app.services.backup import copy_assets
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    run(svc.review('qq','c',draft['id'],[Decision(index=0,action='accept'),Decision(index=1,action='accept'),Decision(index=2,action='reject')]))
    fake_model(monkeypatch,[dict(suggestions()[0],value='新标签',source_ids=[3])])
    run(svc.generate('qq','c','target'))
    expected = run(svc.load_dossier('qq','c'))
    backup = tmp_path/'backup.db'
    counts = copy_assets(store,backup)
    assert counts['contact_dossier_drafts'] == 2 and counts['contact_dossier_rejections'] == 1
    monkeypatch.setattr(database,'DB_PATH',backup)
    run(database.init_db())
    assert run(svc.load_dossier('qq','c')) == expected


def test_all_messages_snapshot_without_sampling(store):
    from app.services import contact_dossier_analysis as analysis
    with sqlite3.connect(store) as db:
        for i in range(20,1020):
            db.execute("INSERT INTO messages(id,platform,chat_id,chat_type,sender_id,content,timestamp) VALUES(?,'qq','large','private','target','hello','2026-10-26T12:00:00')",(i,))
    run_id = run(analysis.prepare('qq','large','target'))
    with sqlite3.connect(store) as db:
        batches = db.execute("SELECT input_json FROM contact_dossier_units WHERE run_id=? ORDER BY unit_key",(run_id,)).fetchall()
    messages = [m for b in batches for m in json.loads(b[0])]
    assert [m['id'] for m in messages] == list(range(20,1020))
    assert all(len(json.loads(b[0])) <= analysis.BATCH_MESSAGES for b in batches)


def test_invalid_model_item_shape_is_ignored(store, monkeypatch):
    fake_model(monkeypatch,[dict(suggestions()[0],kind=['tag'])])
    with pytest.raises(HTTPException) as exc: run(svc.generate('qq','c','target'))
    assert exc.value.status_code == 422


def test_only_confirmed_note_is_visible_and_searchable_in_contacts(store, monkeypatch):
    from app.api.contacts import list_contacts
    from app.services.stats import refresh
    run(refresh())
    fake_model(monkeypatch)
    result = run(svc.generate('qq','c','target'))
    assert not run(list_contacts(q='特殊备注'))
    run(svc.review('qq','c',result['draft']['id'],[Decision(index=0,action='reject'),Decision(index=1,action='accept',value='特殊备注'),Decision(index=2,action='reject')]))
    rows = run(list_contacts(q='特殊备注'))
    assert len(rows) == 1 and rows[0]['local_note'] == '特殊备注'
    assert rows[0]['platform'] == 'qq'


def test_long_message_tail_and_every_message_reach_model(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    text = '完整长消息' * 2300 + '末尾关键业务：机器人维修'
    with sqlite3.connect(store) as db:
        db.execute('UPDATE messages SET content=? WHERE id=1', (text,))
    calls = fake_model(monkeypatch)
    result = run(svc.generate('qq','c','target'))
    parts = [m for prompt in calls if '聊天记录：\n' in prompt for m in json.loads(prompt.split('聊天记录：\n')[-1])]
    assert {m['id'] for m in parts} == set(range(1,13))
    assert ''.join(m['content'] for m in parts if m['id'] == 1) == text
    assert max(len(m['content']) for m in parts) <= analysis.PART_CHARS
    assert result['draft']['coverage']['processed'] == 12
    assert result['job']['status'] == 'done'
    assert result['draft']['items'][0]['source_hashes']['1'] == svc.source_hash(text, '2026-10-26T12:00:00')


def test_failure_resume_skips_successful_batches_and_preserves_old_draft(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    fake_model(monkeypatch)
    old = run(svc.generate('qq','c','target'))['draft']['id']
    monkeypatch.setattr(analysis, 'BATCH_MESSAGES', 4)
    fake_model(monkeypatch)
    normal = svc.AnalyzerService._call_llm
    seen, fail = [], True

    async def call(self, prompt, **kwargs):
        if kwargs['purpose'] == 'contact_dossier_extract':
            first = json.loads(prompt.split('聊天记录：\n')[-1])[0]['id']
            seen.append(first)
            if first == 5 and fail:
                return None
        return await normal(self, prompt, **kwargs)

    monkeypatch.setattr(svc.AnalyzerService, '_call_llm', call)
    with pytest.raises(HTTPException): run(svc.generate('qq','c','target'))
    state = run(svc.load_dossier('qq','c'))
    assert state['draft']['id'] == old and state['current']['tags'] == []
    assert state['job']['processed'] == 4 and state['job']['status'] == 'error'
    # Added after the frozen snapshot: included only by a new run.
    with sqlite3.connect(store) as db:
        db.execute("INSERT INTO messages(id,platform,chat_id,chat_type,sender_id,content,timestamp) VALUES(15,'qq','c','private','target','后来新增','2026-10-27T00:00:00')")
    fail = False
    result = run(svc.generate('qq','c','target',state['job']['id']))
    assert seen == [1,5,5,9]
    assert result['draft']['coverage']['total'] == result['draft']['coverage']['processed'] == 12
    assert result['draft']['id'] != old


def test_restart_preserves_checkpoint_without_auto_call(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    calls = fake_model(monkeypatch)
    run_id = run(analysis.prepare('qq','c','target'))
    run(analysis.recover_interrupted())
    assert not calls
    state = run(svc.load_dossier('qq','c'))
    assert state['job']['status'] == 'interrupted'
    assert run(svc.generate('qq','c','target',run_id))['job']['status'] == 'done'


@pytest.mark.parametrize('bad_id', [11,13,14,999,True,'1'])
def test_extraction_evidence_must_belong_to_current_batch_target(store, monkeypatch, bad_id):
    async def call(self, prompt, **kwargs):
        return json.dumps([dict(fact='错误归属',basis='self_report',source_ids=[bad_id])])
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    with pytest.raises(HTTPException) as exc: run(svc.generate('qq','c','target'))
    assert exc.value.status_code == 422
    assert run(svc.load_dossier('qq','c'))['job']['processed'] == 0


def test_valid_target_from_different_batch_is_rejected(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis, 'BATCH_MESSAGES', 4)
    async def call(self, prompt, **kwargs):
        return json.dumps([dict(fact='未见原文',source_ids=[10])])
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    with pytest.raises(HTTPException): run(svc.generate('qq','c','target'))


def test_final_cannot_cite_target_message_absent_from_facts(store, monkeypatch):
    async def call(self, prompt, **kwargs):
        if kwargs['purpose'] == 'contact_dossier_extract':
            return json.dumps([dict(fact='仅此证据',source_ids=[1])])
        return json.dumps([dict(suggestions()[0],source_ids=[2])])
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    with pytest.raises(HTTPException) as exc: run(svc.generate('qq','c','target'))
    assert exc.value.status_code == 422 and run(svc.load_dossier('qq','c'))['draft'] is None


def test_all_batches_reduced_without_truncating_later_facts(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis,'BATCH_MESSAGES',2)
    seen, reductions = [], []
    async def call(self,prompt,**kwargs):
        if kwargs['purpose'] == 'contact_dossier_extract':
            messages = json.loads(prompt.split('聊天记录：\n')[-1])
            seen.extend(m['id'] for m in messages)
            ids = [m['id'] for m in messages if m['role']=='对方']
            return json.dumps([dict(fact=f'事实{ids}：' + '明确业务记录。'*45,source_ids=ids) for _ in range(10)]) if ids else '[]'
        if kwargs['purpose'] == 'contact_dossier_reduce':
            facts = json.loads(prompt.split('候选事实：\n')[-1])
            ids = sorted({i for f in facts for i in f['source_ids']})
            reductions.extend(ids)
            return json.dumps([dict(fact='汇总业务',source_ids=ids[i:i+8]) for i in range(0,len(ids),8)])
        facts = json.loads(prompt.split('候选事实（附原文编号）：\n')[-1])
        assert {i for f in facts for i in f['source_ids']} == set(range(1,11))
        return json.dumps(suggestions())
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    result = run(svc.generate('qq','c','target'))
    assert seen == list(range(1,13)) and set(reductions) == set(range(1,11))
    assert result['job']['completed_batches'] == 6


def test_snapshot_and_review_detect_source_edits(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    with sqlite3.connect(store) as db: db.execute("UPDATE messages SET content='已更正的原文' WHERE id=1")
    with pytest.raises(HTTPException) as exc:
        run(svc.review('qq','c',draft['id'],[Decision(index=i,action='accept') for i in range(3)]))
    assert exc.value.status_code == 409 and run(svc.load_dossier('qq','c'))['current']['tags'] == []
    run_id = run(analysis.prepare('qq','c','target'))
    with sqlite3.connect(store) as db: db.execute("UPDATE messages SET timestamp='2026-10-28T00:00:00' WHERE id=1")
    with pytest.raises(HTTPException): run(analysis.execute(run_id))
    assert run(svc.load_dossier('qq','c'))['draft']['id'] == draft['id']


def test_manual_edit_during_run_does_not_replace_old_draft(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    fake_model(monkeypatch)
    draft = run(svc.generate('qq','c','target'))['draft']
    run_id = run(analysis.prepare('qq','c','target'))
    run(svc.save_manual('qq','c',NoteUpdate(version=0,short_note='分析中手写')))
    with pytest.raises(HTTPException) as exc: run(analysis.execute(run_id))
    assert exc.value.status_code == 409
    state = run(svc.load_dossier('qq','c'))
    assert state['current']['short_note'] == '分析中手写' and state['draft']['id'] == draft['id']


def test_concurrent_run_and_stale_resume_are_rejected(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    first = run(analysis.prepare('qq','c','target'))
    with pytest.raises(HTTPException) as exc: run(analysis.prepare('qq','c','target'))
    assert exc.value.status_code == 409
    run(analysis.recover_interrupted())
    second = run(analysis.prepare('qq','c','target'))
    run(analysis.recover_interrupted())
    with pytest.raises(HTTPException): run(analysis.prepare('qq','c','target',first))
    assert run(svc.load_dossier('qq','c'))['job']['id'] == second
    old_signature = analysis.model_signature()
    monkeypatch.setattr(analysis,'model_signature',lambda: {**old_signature,'model':'changed'})
    with pytest.raises(HTTPException): run(analysis.prepare('qq','c','target',second))


def test_cancel_preserves_completed_batch(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis,'BATCH_MESSAGES',4)
    fake_model(monkeypatch)
    normal = svc.AnalyzerService._call_llm
    async def scenario():
        waiting = asyncio.Event()
        async def call(self,prompt,**kwargs):
            if kwargs['purpose']=='contact_dossier_extract' and json.loads(prompt.split('聊天记录：\n')[-1])[0]['id']==5:
                waiting.set()
                await asyncio.Future()
            return await normal(self,prompt,**kwargs)
        monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
        run_id = await analysis.prepare('qq','c','target')
        task = asyncio.create_task(analysis.execute(run_id))
        await asyncio.wait_for(waiting.wait(),2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        state = await svc.load_dossier('qq','c')
        assert state['job']['status']=='cancelled' and state['job']['processed']==4
        assert state['draft'] is None
    run(scenario())


@pytest.mark.parametrize('provider',['claude','codex'])
def test_cli_cancellation_reaps_child(store, monkeypatch, provider):
    async def scenario():
        waiting = asyncio.Event()
        class Process:
            pid = 99999999
            returncode = None
            killed = False
            reaped = False
            async def communicate(self, *args):
                waiting.set()
                await asyncio.Future()
            def kill(self): self.killed = True; self.returncode = -9
            async def wait(self): self.reaped = True
        child = Process()
        if provider == 'codex':
            import os
            monkeypatch.setattr(os,'killpg',lambda pid,sig: child.kill())
        async def spawn(*args,**kwargs): return child
        monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
        analyzer = svc.AnalyzerService()
        task = asyncio.create_task(getattr(analyzer,f'_call_{provider}_cli')('synthetic-only'))
        await asyncio.wait_for(waiting.wait(),2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert child.killed and child.reaped
    run(scenario())


def test_codex_uses_chatgpt_quota_stdin_isolation_and_actual_usage(store, monkeypatch):
    from pathlib import Path
    from app.services import budget
    monkeypatch.setenv('OPENAI_API_KEY','test-key-must-not-be-inherited')
    monkeypatch.setenv('CODEX_API_KEY','test-key-must-not-be-inherited')
    monkeypatch.setenv('OPENAI_BASE_URL','https://invalid.test')
    seen = {}
    async def spawn(*args,**kwargs):
        seen.update(args=args,kwargs=kwargs)
        class Process:
            returncode = 0
            async def communicate(self, payload):
                seen['payload'] = payload
                Path(args[args.index('--output-last-message')+1]).write_text('[{"ok":true}]')
                return json.dumps({'type':'turn.completed','usage':{'input_tokens':1234,'output_tokens':42}}).encode(), b''
        return Process()
    async def record(*args): seen['usage'] = args
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(budget,'record',record)
    analyzer = svc.AnalyzerService()
    analyzer.cfg.llm.model = 'gpt-6.1-sol'
    assert run(analyzer._call_codex_cli('private synthetic transcript','contact_dossier_extract')) == '[{"ok":true}]'
    assert seen['payload'] == b'private synthetic transcript'
    assert 'private synthetic transcript' not in seen['args']
    assert 'forced_login_method="chatgpt"' in seen['args'] and '--ignore-user-config' in seen['args']
    assert seen['kwargs']['start_new_session'] is True
    assert seen['args'][seen['args'].index('-m')+1] == 'gpt-6.1-sol'
    assert all(k not in seen['kwargs']['env'] for k in ('OPENAI_API_KEY','CODEX_API_KEY','OPENAI_BASE_URL'))
    assert not Path(seen['kwargs']['cwd']).exists()
    assert seen['usage'][0:4] == ('codex_cli','gpt-6.1-sol',1234,42)


def test_parallel_batches_are_bounded_ordered_and_checkpointed(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis,'EXTRACT_CONCURRENCY',3)
    monkeypatch.setattr(analysis,'BATCH_MESSAGES',2)
    active, peak, order = 0, 0, []
    started = asyncio.Event()
    async def call(self,prompt,**kwargs):
        nonlocal active, peak
        if kwargs['purpose'] == 'contact_dossier_extract':
            messages=json.loads(prompt.split('聊天记录：\n')[-1])
            first=messages[0]['id']
            active+=1;peak=max(peak,active)
            if active==3:started.set()
            try:
                await asyncio.wait_for(started.wait(),1)
                await asyncio.sleep(0.02 if first==1 else 0.001)
                order.append(first)
                ids=[m['id'] for m in messages if m['role']=='对方']
                return json.dumps([dict(fact=str(first),source_ids=ids)]) if ids else '[]'
            finally:active-=1
        facts=json.loads(prompt.split('候选事实（附原文编号）：\n')[-1])
        assert [int(f['fact']) for f in facts] == [1,3,5,7,9]
        return json.dumps(suggestions())
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    state=run(svc.generate('qq','c','target'))
    assert peak==3 and order[0]!=1 and active==0
    assert state['job']['processed']==12 and state['job']['completed_batches']==6


def test_parallel_failure_stops_and_awaits_other_model_calls(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    monkeypatch.setattr(analysis,'EXTRACT_CONCURRENCY',3)
    monkeypatch.setattr(analysis,'BATCH_MESSAGES',2)
    async def scenario():
        active=0
        started=asyncio.Event()
        async def call(self,prompt,**kwargs):
            nonlocal active
            first=json.loads(prompt.split('聊天记录：\n')[-1])[0]['id']
            active+=1
            if active==3:started.set()
            try:
                await started.wait()
                if first==1:return None
                await asyncio.Future()
            finally:active-=1
        monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
        with pytest.raises(HTTPException):await svc.generate('qq','c','target')
        assert active==0
        state=await svc.load_dossier('qq','c')
        assert state['job']['status']=='error' and state['draft'] is None
    run(scenario())


def test_overlong_reduction_is_corrected_once_and_cached(store, monkeypatch):
    from app.services import contact_dossier_analysis as analysis
    calls=[]
    async def call(self,prompt,**kwargs):
        calls.append(prompt)
        if len(calls)==1:
            return json.dumps([dict(fact='长'*500,source_ids=[1]) for _ in range(12)])
        return json.dumps([dict(fact='合并后保留原文依据',source_ids=[1])])
    monkeypatch.setattr(svc.AnalyzerService,'_call_llm',call)
    async def scenario():
        run_id=await analysis.prepare('qq','c','target')
        inputs=[dict(fact='输入事实',source_ids=[1])]
        args=(svc.AnalyzerService(),run_id,'reduce:test','reduce',inputs,'合成汇总任务',{1})
        result=await analysis.checkpoint_call(*args)
        assert len(calls)==2 and '格式修正' in calls[1]
        assert result[0]['source_ids']==[1]
        assert await analysis.checkpoint_call(*args)==result and len(calls)==2
    run(scenario())
