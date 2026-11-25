"""People API: the person layer behind 联系人档案."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core import database
from app.services import persons as svc

router = APIRouter()


class PersonCreate(BaseModel):
    name: str
    notes: Optional[str] = None


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class IdentityCreate(BaseModel):
    platform: str
    kind: str                 # 'chat' | 'sender'
    value: str
    chat_id: str = ""         # sender scope; '' = platform-wide
    display_name: Optional[str] = None


class MergeRequest(BaseModel):
    into: int


class LinkChatRequest(BaseModel):
    """Attach a chat (and optionally sender identities) to a person, creating
    the person by name if needed. This is what the profile page's 身份卡 calls."""
    name: str
    platform: str
    chat_id: str
    chat_name: Optional[str] = None
    senders: list[IdentityCreate] = []


@router.get("/api/persons")
async def list_persons(q: Optional[str] = None):
    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            """SELECT p.id, p.name, p.notes, p.created_at,
                      COUNT(i.id) AS identities,
                      GROUP_CONCAT(DISTINCT i.platform) AS platforms
               FROM persons p LEFT JOIN person_identities i ON i.person_id = p.id
               WHERE (? IS NULL OR p.name LIKE ?)
               GROUP BY p.id ORDER BY p.name COLLATE NOCASE""",
            (q, f"%{q}%" if q else None),
        )
    return [dict(r) | {"platforms": (r["platforms"] or "").split(",") if r["platforms"] else []} for r in rows]


@router.post("/api/persons")
async def create_person(body: PersonCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    async with database.connect_db() as db:
        pid = await svc.ensure_person(db, name)
        if body.notes is not None:
            await db.execute("UPDATE persons SET notes = ? WHERE id = ?", (body.notes, pid))
        await db.commit()
    return {"id": pid, "name": name}


@router.get("/api/persons/for-chat/{platform}/{chat_id}")
async def person_for_chat(platform: str, chat_id: str, chat_name: Optional[str] = None):
    """The person behind a chat (or null) plus sender-identity candidates."""
    async with database.connect_db() as db:
        person = await svc.person_for_chat(db, platform, chat_id)
        detail = None
        if person:
            detail = await svc.footprint(db, int(person["id"]))
        if chat_name is None:
            row = await db.execute_fetchall(
                "SELECT chat_name FROM stats_chats WHERE platform = ? AND chat_id = ? LIMIT 1", (platform, chat_id),
            )
            chat_name = row[0][0] if row else None
        suggestions = await svc.suggest_for_chat(db, platform, chat_id, chat_name)
    return {"person": person, "footprint": detail, "suggestions": suggestions}


@router.post("/api/persons/link-chat")
async def link_chat(body: LinkChatRequest):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "姓名不能为空")
    async with database.connect_db() as db:
        pid = await svc.ensure_person(db, name)
        await svc.add_identity(
            db, pid, platform=body.platform, kind="chat", value=body.chat_id, display_name=body.chat_name,
        )
        for s in body.senders:
            await svc.add_identity(
                db, pid, platform=s.platform, kind=s.kind, value=s.value, chat_id=s.chat_id,
                display_name=s.display_name, source="auto",
            )
        await db.commit()
    return {"id": pid, "name": name}


@router.get("/api/persons/{person_id}")
async def get_person(person_id: int):
    async with database.connect_db() as db:
        rows = await db.execute_fetchall("SELECT id, name, notes, created_at FROM persons WHERE id = ?", (person_id,))
        if not rows:
            raise HTTPException(404, "人物不存在")
        fp = await svc.footprint(db, person_id)
    return dict(rows[0]) | fp


@router.put("/api/persons/{person_id}")
async def update_person(person_id: int, body: PersonUpdate):
    async with database.connect_db() as db:
        if body.name is not None:
            await db.execute("UPDATE persons SET name = ? WHERE id = ?", (body.name.strip(), person_id))
        if body.notes is not None:
            await db.execute("UPDATE persons SET notes = ? WHERE id = ?", (body.notes, person_id))
        await db.commit()
    return {"ok": True}


@router.delete("/api/persons/{person_id}")
async def delete_person(person_id: int):
    async with database.connect_db() as db:
        await db.execute("DELETE FROM person_identities WHERE person_id = ?", (person_id,))
        cur = await db.execute("DELETE FROM persons WHERE id = ?", (person_id,))
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "人物不存在")
    return {"ok": True}


@router.post("/api/persons/{person_id}/identities")
async def add_identity(person_id: int, body: IdentityCreate):
    async with database.connect_db() as db:
        exists = await db.execute_fetchall("SELECT 1 FROM persons WHERE id = ?", (person_id,))
        if not exists:
            raise HTTPException(404, "人物不存在")
        try:
            await svc.add_identity(
                db, person_id, platform=body.platform, kind=body.kind, value=body.value.strip(),
                chat_id=body.chat_id, display_name=body.display_name,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        await db.commit()
    return {"ok": True}


@router.delete("/api/persons/{person_id}/identities/{identity_id}")
async def remove_identity(person_id: int, identity_id: int):
    async with database.connect_db() as db:
        cur = await db.execute(
            "DELETE FROM person_identities WHERE id = ? AND person_id = ?", (identity_id, person_id),
        )
        pruned = await svc.prune_empty(db, person_id)
        await db.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "身份不存在")
    return {"ok": True, "person_deleted": pruned}


@router.post("/api/persons/{person_id}/merge")
async def merge_person(person_id: int, body: MergeRequest):
    if body.into == person_id:
        raise HTTPException(400, "不能合并到自己")
    async with database.connect_db() as db:
        for pid in (person_id, body.into):
            if not await db.execute_fetchall("SELECT 1 FROM persons WHERE id = ?", (pid,)):
                raise HTTPException(404, f"人物 {pid} 不存在")
        await svc.merge(db, person_id, body.into)
        await db.commit()
    return {"ok": True, "id": body.into}
