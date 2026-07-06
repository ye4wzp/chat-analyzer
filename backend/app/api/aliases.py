"""Cross-platform identity: contact_aliases maps (platform, platform_id) to a
canonical person. VIP whitelisting and pre-filtering resolve through it, so a
display-name change on one platform no longer breaks VIP status."""
import aiosqlite
from fastapi import APIRouter, HTTPException

from app.core import database
from app.models.aliases import AliasCreate

router = APIRouter()


@router.get("/api/aliases")
async def list_aliases():
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """SELECT id, canonical_name, platform, platform_id, platform_name
               FROM contact_aliases ORDER BY canonical_name, platform"""
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
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        await db.execute(
            """INSERT INTO contact_aliases (canonical_name, platform, platform_id, platform_name)
               VALUES (?,?,?,?)
               ON CONFLICT(platform, platform_id) DO UPDATE SET
                   canonical_name=excluded.canonical_name,
                   platform_name=excluded.platform_name""",
            (body.canonical_name.strip(), body.platform, body.platform_id.strip(), body.platform_name),
        )
        await db.commit()
    return {"ok": True}


@router.delete("/api/aliases/{alias_id}")
async def delete_alias(alias_id: int):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=60) as db:
        cur = await db.execute("DELETE FROM contact_aliases WHERE id=?", (alias_id,))
        await db.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "别名不存在")
    return {"ok": True}
