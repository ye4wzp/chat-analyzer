import aiosqlite
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core import database
from app.services.wechat_articles import ReadError, article_links, fetch_body

router = APIRouter(prefix="/api/messages/{message_id}/wechat-articles")


class SaveArticle(BaseModel):
    url: str = Field(max_length=4096)
    text: str | None = Field(default=None, max_length=1_000_000)


async def message_links(db, message_id):
    rows = await db.execute_fetchall(
        "SELECT content,raw_data FROM messages WHERE id=? AND platform='wechat' AND chat_id GLOB 'gh_*'",
        (message_id,),
    )
    if not rows:
        raise HTTPException(404, "找不到这条公众号消息")
    return article_links(*rows[0])


async def saved_article(db, url):
    rows = await db.execute_fetchall("SELECT body, source, saved_at FROM wechat_articles WHERE url=?", (url,))
    return dict(rows[0]) if rows else {}


@router.get("")
async def read_articles(message_id: int):
    async with aiosqlite.connect(str(database.DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        links = await message_links(db, message_id)
        return [{**item, **await saved_article(db, item["url"])} for item in links]


@router.post("")
async def save_article(message_id: int, request: SaveArticle):
    async with aiosqlite.connect(str(database.DB_PATH), timeout=30) as db:
        db.row_factory = aiosqlite.Row
        links = await message_links(db, message_id)
        item = next((i for i in links if i["url"] == request.url), None)
        if not item:
            raise HTTPException(400, "这篇文章不属于当前推送")
        archived = await saved_article(db, request.url)
        if archived:
            return {**item, **archived}
        if request.text is None:
            try:
                body = await fetch_body(request.url)
            except ReadError as exc:
                raise HTTPException(422, str(exc)) from exc
            source = "web"
        else:
            body = request.text.strip()
            if not body:
                raise HTTPException(422, "请粘贴文章正文")
            source = "manual"
        await db.execute(
            "INSERT INTO wechat_articles(url,title,body,source) VALUES(?,?,?,?) ON CONFLICT(url) DO NOTHING",
            (request.url, item["title"], body, source),
        )
        await db.commit()
        return {**item, **await saved_article(db, request.url)}
