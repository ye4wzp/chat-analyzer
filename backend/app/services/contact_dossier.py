"""Evidence-bound local contact drafts; all effective changes require review."""
import hashlib
import json

from fastapi import HTTPException

from app.core import database
from app.services.analyzer import AnalyzerService
from app.services.tagger import get_or_create_tag

SENDER_KEY = "COALESCE(NULLIF(sender_id,''),NULLIF(sender_name,''),'?')"
LIMITS = {"tag": 20, "short_note": 80, "detail_note": 1500}


def fingerprint(kind, value):
    return hashlib.sha256((kind + ":" + " ".join(value.casefold().split())).encode()).hexdigest()


def source_hash(content, timestamp):
    return hashlib.sha256(json.dumps([content or '', timestamp], ensure_ascii=False).encode()).hexdigest()


async def ensure_private(db, platform, chat_id):
    rows = await db.execute_fetchall(
        "SELECT chat_type,chat_name FROM messages WHERE platform=? AND chat_id=? ORDER BY timestamp DESC,id DESC LIMIT 1",
        (platform, chat_id))
    if not rows:
        raise HTTPException(404, "该联系人没有聊天记录")
    if rows[0]["chat_type"] != "private":
        raise HTTPException(400, "第一版仅支持私聊联系人；群聊不能当作一个人生成档案")
    return rows[0]


async def effective(db, platform, chat_id):
    rows = await db.execute_fetchall("SELECT * FROM contact_dossiers WHERE platform=? AND chat_id=?", (platform, chat_id))
    result = dict(rows[0]) if rows else dict(short_note="", detail_note="", short_locked=False,
        detail_locked=False, version=0, evidence_json="{}", contact_sender_key=None, updated_at=None)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    result["short_locked"] = bool(result["short_locked"])
    result["detail_locked"] = bool(result["detail_locked"])
    result["tags"] = [dict(r) for r in await db.execute_fetchall(
        """SELECT t.id,t.name,l.reason FROM contact_tag_links l JOIN contact_tags t ON t.id=l.tag_id
           WHERE l.platform=? AND l.chat_id=? AND l.status='confirmed' ORDER BY t.name""", (platform, chat_id))]
    return result


def draft_dict(row):
    d = dict(row)
    d["coverage"] = json.loads(d.pop("coverage_json"))
    d["items"] = json.loads(d.pop("items_json"))
    return d


async def load_dossier(platform, chat_id):
    async with database.connect_db() as db:
        await ensure_private(db, platform, chat_id)
        current = await effective(db, platform, chat_id)
        senders = [dict(r) for r in await db.execute_fetchall(
            f"SELECT {SENDER_KEY} AS key, MAX(sender_name) AS name,COUNT(*) AS count FROM messages WHERE platform=? AND chat_id=? GROUP BY {SENDER_KEY} ORDER BY count DESC LIMIT 100",
            (platform, chat_id))]
        rows = await db.execute_fetchall(
            "SELECT * FROM contact_dossier_drafts WHERE platform=? AND chat_id=? AND status='pending' ORDER BY created_at DESC,rowid DESC LIMIT 1", (platform, chat_id))
        from app.services.contact_dossier_analysis import latest_job
        return {"current": current, "senders": senders, "draft": draft_dict(rows[0]) if rows else None,
                "job": await latest_job(db, platform, chat_id)}




def normalize_items(raw, allowed_ids, current):
    if not isinstance(raw, list):
        return []
    out, seen, kinds = [], set(), {}
    for item in raw:
        if not isinstance(item, dict): continue
        kind, value = item.get("kind"), item.get("value")
        if not isinstance(kind, str) or kind not in LIMITS or not isinstance(value, str): continue
        value = value.strip()
        if not value or len(value) > LIMITS[kind]: continue
        if kind != "tag" and current.get(kind.replace("_note", "_locked")): continue
        if kind == "tag" and value in {t["name"] for t in current["tags"]}: continue
        ids = item.get("source_ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8: continue
        if any(type(i) is not int or i not in allowed_ids for i in ids): continue
        key = fingerprint(kind, value)
        if key in seen or kinds.get(kind, 0) >= (5 if kind == "tag" else 1): continue
        seen.add(key); kinds[kind] = kinds.get(kind, 0) + 1
        out.append(dict(kind=kind, value=value, source_ids=sorted(set(ids)),
            basis=item.get("basis") if item.get("basis") in ("self_report", "interaction") else "unverified",
            reason=str(item.get("reason") or "")[:200]))
    return out


async def generate(platform, chat_id, sender_key, resume_run_id=None):
    from app.services import contact_dossier_analysis as analysis
    run_id = await analysis.prepare(platform, chat_id, sender_key, resume_run_id)
    await analysis.execute(run_id)
    return await load_dossier(platform, chat_id)


async def save_manual(platform, chat_id, body):
    async with database.connect_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        await ensure_private(db, platform, chat_id)
        current = await effective(db, platform, chat_id)
        if current['version'] != body.version:
            raise HTTPException(409, "档案已更新，请刷新后再保存")
        evidence = current['evidence']
        for key in ('short_note', 'detail_note'):
            if getattr(body, key) != current[key]: evidence.pop(key, None)
        await db.execute("""INSERT INTO contact_dossiers(platform,chat_id,short_note,detail_note,short_locked,detail_locked,version,evidence_json)
            VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(platform,chat_id) DO UPDATE SET
            short_note=excluded.short_note,detail_note=excluded.detail_note,short_locked=excluded.short_locked,
            detail_locked=excluded.detail_locked,version=excluded.version,evidence_json=excluded.evidence_json,updated_at=CURRENT_TIMESTAMP""",
            (platform,chat_id,body.short_note,body.detail_note,body.short_locked,body.detail_locked,current['version']+1,json.dumps(evidence,ensure_ascii=False)))
        await db.commit()
    return await load_dossier(platform, chat_id)


async def review(platform, chat_id, draft_id, decisions):
    async with database.connect_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        await ensure_private(db, platform, chat_id)
        rows = await db.execute_fetchall("SELECT * FROM contact_dossier_drafts WHERE id=? AND platform=? AND chat_id=?", (draft_id, platform, chat_id))
        if not rows: raise HTTPException(404, "草稿不存在")
        draft = draft_dict(rows[0])
        if draft['status'] != 'pending': raise HTTPException(409, "草稿已审核或被新草稿替代")
        current = await effective(db, platform, chat_id)
        if current['version'] != draft['base_version']: raise HTTPException(409, "当前备注已变化，请重新生成，避免覆盖新内容")
        items = draft['items']
        if len(decisions) != len(items) or {d.index for d in decisions} != set(range(len(items))):
            raise HTTPException(400, "请对草稿的每一项明确选择采纳或拒绝")
        evidence = current['evidence']
        for decision in decisions:
            item = items[decision.index]
            kind = item['kind']
            if decision.action == 'reject':
                key = fingerprint(kind,item['value'])
                old = await db.execute_fetchall("SELECT source_ids FROM contact_dossier_rejections WHERE platform=? AND chat_id=? AND fingerprint=?", (platform,chat_id,key))
                ids = sorted(set(draft['coverage'].get('target_ids', item['source_ids'])) | (set(json.loads(old[0][0])) if old else set()))
                await db.execute("INSERT OR REPLACE INTO contact_dossier_rejections VALUES(?,?,?,?)", (platform,chat_id,key,json.dumps(ids)))
                item['decision'] = 'rejected'
                continue
            if kind != 'tag' and current[kind.replace('_note','_locked')]:
                raise HTTPException(409, "该备注已锁定，请先处理锁定状态并重新生成")
            value = (decision.value if decision.value is not None else item['value']).strip()
            if not value or len(value) > LIMITS[kind]: raise HTTPException(400, "采纳内容为空或超过长度限制")
            ids = item['source_ids']
            ph = ','.join('?' for _ in ids)
            found = await db.execute_fetchall(f"SELECT id,content,timestamp FROM messages WHERE platform=? AND chat_id=? AND {SENDER_KEY}=? AND id IN ({ph})", (platform,chat_id,draft['contact_sender_key'],*ids))
            if len(found) != len(ids): raise HTTPException(409, "部分原文已不存在或不属于该联系人，请重新生成")
            if item.get('source_hashes') and any(item['source_hashes'].get(str(r['id'])) != source_hash(r['content'], r['timestamp']) for r in found):
                raise HTTPException(409, "引用原文在生成后已变化，请重新生成草稿")
            item.update(decision='accepted', reviewed_value=value, edited=value != item['value'])
            proof = {**item, 'draft_id':draft_id}
            if kind == 'tag':
                tag_id = await get_or_create_tag(db,value,source='ai',status='active')
                await db.execute("UPDATE contact_tags SET status='active' WHERE id=?", (tag_id,))
                old = await db.execute_fetchall("SELECT status FROM contact_tag_links WHERE platform=? AND chat_id=? AND tag_id=?", (platform,chat_id,tag_id))
                if old and old[0][0] == 'confirmed': continue
                await db.execute("""INSERT INTO contact_tag_links(platform,chat_id,tag_id,reason,source,status,batch_id)
                    VALUES(?,?,?,?,'ai','confirmed',?) ON CONFLICT(platform,chat_id,tag_id) DO UPDATE SET
                    reason=excluded.reason,source='ai',status='confirmed',batch_id=excluded.batch_id""", (platform,chat_id,tag_id,item['reason'],draft_id))
                evidence[f'tag:{tag_id}'] = proof
            else:
                current[kind] = value
                current[kind.replace('_note','_locked')] = True
                evidence[kind] = proof
        await db.execute("""INSERT INTO contact_dossiers(platform,chat_id,short_note,detail_note,short_locked,detail_locked,version,evidence_json,contact_sender_key)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,chat_id) DO UPDATE SET
            short_note=excluded.short_note,detail_note=excluded.detail_note,short_locked=excluded.short_locked,
            detail_locked=excluded.detail_locked,version=excluded.version,evidence_json=excluded.evidence_json,
            contact_sender_key=excluded.contact_sender_key,updated_at=CURRENT_TIMESTAMP""", (platform,chat_id,current['short_note'],current['detail_note'],current['short_locked'],current['detail_locked'],current['version']+1,json.dumps(evidence,ensure_ascii=False),draft['contact_sender_key']))
        await db.execute("UPDATE contact_dossier_drafts SET status='reviewed',reviewed_at=CURRENT_TIMESTAMP,items_json=? WHERE id=?", (json.dumps(items,ensure_ascii=False),draft_id))
        await db.commit()
    return await load_dossier(platform,chat_id)
