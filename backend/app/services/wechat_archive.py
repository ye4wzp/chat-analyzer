"""Durable per-account scan offsets. Both historical and future pushes enter the same queue."""
import asyncio
import logging

import aiosqlite

from app.core import database
from app.services.wechat_articles import ReadError, WechatBusy, article_links, fetch_body

# The partial messages index matches this predicate. Checking idle subscriptions
# does not require a scan of the user's full message store.
PUBLIC = "platform='wechat' AND chat_id GLOB 'gh_*'"
UNSCANNED = f"EXISTS (SELECT 1 FROM messages m WHERE {PUBLIC} AND m.chat_id=a.chat_id AND m.id>a.last_id)"


async def scan_batch(db):
    accounts = await db.execute_fetchall(
        f"SELECT a.chat_id,a.last_id FROM wechat_archive_accounts a WHERE enabled=1 AND {UNSCANNED} ORDER BY last_id LIMIT 1"
    )
    if not accounts:
        return False
    chat_id, cursor = accounts[0]
    rows = await db.execute_fetchall(
        f"SELECT id,content,raw_data FROM messages WHERE {PUBLIC} AND chat_id=? AND id>? ORDER BY id LIMIT 32",
        (chat_id, cursor),
    )
    if not rows:
        return False
    items = [(chat_id, link["url"], link["title"], mid)
             for mid, content, raw in rows for link in article_links(content, raw)]
    # A user may pause or remove this account while messages are being parsed.
    changed = await db.execute("UPDATE wechat_archive_accounts SET last_id=? WHERE chat_id=? AND enabled=1 AND last_id=?",
                               (rows[-1][0], chat_id, cursor))
    if changed.rowcount:
        await db.executemany(
            "INSERT OR IGNORE INTO wechat_archive_items(chat_id,url,title,message_id) VALUES(?,?,?,?)", items)
    await db.commit()
    return True


async def fetch_next(db):
    cooldown = await db.execute_fetchall("SELECT MAX(cooldown_until) > unixepoch() FROM wechat_archive_items")
    if cooldown[0][0]:
        return False
    rows = await db.execute_fetchall("""
        SELECT i.url,i.title FROM wechat_archive_items i JOIN wechat_archive_accounts a USING(chat_id)
        WHERE a.enabled=1 AND i.error IS NULL AND NOT EXISTS (SELECT 1 FROM wechat_articles s WHERE s.url=i.url)
        ORDER BY i.message_id DESC LIMIT 1""")
    if not rows:
        return False
    url, title = rows[0]
    try:
        body = await fetch_body(url)
    except ReadError as exc:
        until = exc.delay if isinstance(exc, WechatBusy) else 0
        await db.execute("""UPDATE wechat_archive_items SET error=?,cooldown_until=CASE WHEN ?>0 THEN unixepoch()+? ELSE 0 END
                            WHERE url=? AND chat_id IN (SELECT chat_id FROM wechat_archive_accounts WHERE enabled=1)""",
                         (str(exc), until, until, url))
    else:
        # A concurrent manual save wins. Re-check subscription after the network
        # await, so disabling the last interested account also cancels this write.
        await db.execute("""INSERT OR IGNORE INTO wechat_articles(url,title,body,source)
            SELECT ?,?,?,'web' WHERE EXISTS (SELECT 1 FROM wechat_archive_items i
                JOIN wechat_archive_accounts a USING(chat_id) WHERE a.enabled=1 AND i.url=?)""",
                         (url, title, body, url))
    await db.commit()
    return True


async def _loop(operation, active_interval):
    while True:
        delay = 5
        try:
            async with aiosqlite.connect(database.DB_PATH, timeout=30) as db:
                if await operation(db):
                    delay = active_interval
        except Exception:
            logging.exception("WeChat article archive: %s", operation.__name__)
        await asyncio.sleep(delay)


async def run_archive():
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_loop(scan_batch, 0.1))
        tasks.create_task(_loop(fetch_next, 2))
