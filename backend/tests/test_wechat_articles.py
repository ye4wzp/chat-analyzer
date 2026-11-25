import asyncio
import sqlite3

import httpx
import pytest
from fastapi import HTTPException

from app.api import wechat_articles as api
from app.core import database
from app.services import wechat_articles as articles

U1 = "https://mp.weixin.qq.com/s/article_1"
U2 = "https://mp.weixin.qq.com/s/article_2"
PUSH = f"""<msg><appmsg><title>主推</title><url>{U1}</url><mmreader><category>
<item><title>第一篇</title><url>{U1}?scene=1</url></item>
<item><title>第二篇</title><url>{U2}</url></item></category></mmreader></appmsg></msg>"""


def test_push_preserves_order_and_rejects_unrelated_urls():
    assert articles.article_links(PUSH) == [{"title": "第一篇", "url": U1}, {"title": "第二篇", "url": U2}]
    assert articles.article_url("http://mp.weixin.qq.com/s?__biz=abc&amp;mid=7&amp;idx=1&amp;scene=5") == (
        "https://mp.weixin.qq.com/s?__biz=abc&mid=7&idx=1")
    assert articles.article_url("https://mp.weixin.qq.com.example.org/s/test") is None


def test_save_and_reopen(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "chat.db")
    asyncio.run(database.init_db())
    with sqlite3.connect(database.DB_PATH) as db:
        db.execute("INSERT INTO messages(id,platform,chat_id,content,timestamp) VALUES(1,'wechat','gh_test',?,'2026-01-01')", (PUSH,))
    requests = []

    def respond(req):
        requests.append(str(req.url))
        if str(req.url) == U2:
            return httpx.Response(302, headers={"location": "/mp/wappoc_appmsgcaptcha"})
        return httpx.Response(200, text='<div id="js_content"><p>正文<b>加粗</b></p><script>无关</script><p>第二段</p></div>推荐文章')

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **opts: client(transport=httpx.MockTransport(respond), **opts))

    async def check():
        with pytest.raises(HTTPException) as exc:
            await api.save_article(1, api.SaveArticle(url="https://example.org/"))
        assert exc.value.status_code == 400 and not requests
        saved = await api.save_article(1, api.SaveArticle(url=U1))
        assert saved["body"] == "正文加粗\n第二段" and saved["source"] == "web"
        assert saved == await api.save_article(1, api.SaveArticle(url=U1))
        with pytest.raises(HTTPException, match="安全验证"):
            await api.save_article(1, api.SaveArticle(url=U2))
        assert "body" not in (await api.read_articles(1))[1]
        manual = await api.save_article(1, api.SaveArticle(url=U2, text=" 自行复制的文字 "))
        assert manual["source"] == "manual" and manual["body"] == "自行复制的文字"
        assert await api.read_articles(1) == [saved, manual]
        assert requests == [U1, U2]

    asyncio.run(check())
