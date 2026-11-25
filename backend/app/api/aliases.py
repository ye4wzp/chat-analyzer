"""Legacy alias endpoints, now a thin view over persons / person_identities
(kind='chat'). Kept so the Settings 别名卡 and the CLI keep working; new code
should use /api/persons."""
from fastapi import APIRouter, HTTPException

from app.core import database
from app.models.aliases import AliasCreate
from app.services import persons as svc

router = APIRouter()


@router.get("/api/aliases")
async def list_aliases():
    async with database.connect_db() as db:
        rows = await db.execute_fetchall(
            """SELECT i.id, p.name AS canonical_name, i.platform, i.value AS platform_id, i.display_name AS platform_name
               FROM person_identities i JOIN persons p ON p.id = i.person_id
               WHERE i.kind = 'chat' ORDER BY p.name, i.platform"""
        )
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        grouped.setdefault(d.pop("canonical_name"), []).append(d)
    return [{"canonical_name": name, "entries": entries} for name, entries in grouped.items()]


@router.post("/api/aliases")
async def add_alias(body: AliasCreate):
    if not body.canonical_name.strip() or not body.platform_id.strip():
        raise HTTPException(400, "canonical_name 与 platform_id 不能为空")
    async with database.connect_db() as db:
        pid = await svc.ensure_person(db, body.canonical_name.strip())
        await svc.add_identity(
            db, pid, platform=body.platform, kind="chat", value=body.platform_id.strip(),
            display_name=body.platform_name,
        )
        await db.commit()
    return {"ok": True, "person_id": pid}


@router.delete("/api/aliases/{alias_id}")
async def delete_alias(alias_id: int):
    async with database.connect_db() as db:
        rows = await db.execute_fetchall("SELECT person_id FROM person_identities WHERE id = ?", (alias_id,))
        if not rows:
            raise HTTPException(404, "别名不存在")
        await db.execute("DELETE FROM person_identities WHERE id = ?", (alias_id,))
        await svc.prune_empty(db, int(rows[0][0]))
        await db.commit()
    return {"ok": True}
